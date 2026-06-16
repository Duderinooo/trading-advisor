#!/usr/bin/env python3
"""Event-driven trading advisor — main entry point.

Slim orchestrator. Service logic lives in services/, scheduling in runtime/.
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import config
from core import (
    load_portfolio, save_portfolio, portfolio_lock,
    maybe_auto_kill, get_daily_usage,
)
from core.data.livefeed import live_quote_for_ticker
from notifier import send_alert
from telegram_listener import start_listener_thread

from runtime.scheduler import (
    is_market_hours, is_morning_prep_time, is_xetra_open_check_time,
    is_us_open_check_time, is_eod_summary_time, is_weekend_summary_time,
    is_weekend_news_window, morning_prep_done_today,
    heartbeat_watchdog, startup_cleanup,
)
from runtime.state import AppState
from services.event_monitor import run_event_check
from services.morning_brief import run_morning_prep
from services.news_monitor import run_news_check, run_berkshire_check
from services.opening_check import run_opening_check
from services.price_monitor import run_price_check, ratchet_open_trade_extremes
from services.web_commands import drain_web_commands
from services.risk_guards import check_equity_alerts, check_exit_reminders
from services.summary import run_eod_summary, run_weekend_summary


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Root logger → rotating file only. No StreamHandler: launchd's bot.err only catches
# pre-logging-init crashes (small) instead of the ~MB/day yfinance noise.
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
for _h in list(_root_logger.handlers):
    _root_logger.removeHandler(_h)
_file_handler = RotatingFileHandler(_LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
_root_logger.addHandler(_file_handler)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)


class _YFinanceNoiseFilter(logging.Filter):
    """Drop expected yfinance ERROR-spam (404s on ETFs without fundamentals,
    transient 'possibly delisted' API hickups). Both handled in market_data."""
    _SUPPRESS = ("possibly delisted", "No fundamentals data found")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(s in msg for s in self._SUPPRESS)


logging.getLogger("yfinance").addFilter(_YFinanceNoiseFilter())
logger = logging.getLogger("trading_advisor")


# ---------------------------------------------------------------------------
# Heartbeat persistence — writes portfolio.heartbeat for the web dashboard
# ---------------------------------------------------------------------------

HEARTBEAT_WRITE_INTERVAL_SEC = 60  # ≤1×/min — web dashboard stale-warn fires at 120s.


def _collect_live_quotes(pf: dict) -> tuple[dict[str, float], dict[str, dict]]:
    """Scrape ls-tc for all open + watched tickers. Skips yfinance entirely."""
    live_prices: dict[str, float] = {}
    live_quotes: dict[str, dict] = {}
    tickers = list({
        *(t["ticker"] for t in pf.get("open_trades", []) or [] if t.get("ticker")),
        *(w["ticker"] for w in pf.get("watch_levels", []) or [] if w.get("ticker")),
    })
    for tk in tickers:
        q = live_quote_for_ticker(tk)
        if not q or not q.get("price"):
            continue
        live_prices[tk] = float(q["price"])
        live_quotes[tk] = {
            "price": q.get("price"),
            "bid": q.get("bid"),
            "ask": q.get("ask"),
            "ts": q.get("ts"),
            "change_pct": q.get("change_pct"),
            "market_status": q.get("market_status"),
            "source": q.get("source"),
        }
    return live_prices, live_quotes


def _append_equity_point(pf: dict, unrealized_eur: float, now: datetime) -> None:
    """Append intraday equity snapshot. Prune to 14d rolling. No-op when no live data."""
    starting = float(
        pf.get("total_capital_eur", config.BUDGET_EUR) or config.BUDGET_EUR
    )
    realized = sum(
        float(t.get("pnl_eur") or 0)
        for t in pf.get("closed_trades", []) or []
    )
    movements = sum(
        float(m.get("amount") or 0)
        for m in pf.get("cash_movements", []) or []
    )
    equity_now = round(starting + realized + movements + unrealized_eur, 2)
    hist = pf.get("equity_history", []) or []
    ts = now.strftime("%Y-%m-%d %H:%M")
    if not hist or hist[-1].get("ts") != ts:
        hist.append({
            "ts": ts,
            "equity": equity_now,
            "unrealized": round(unrealized_eur, 2),
            "market_hours": is_market_hours(),
        })
    cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M")
    pf["equity_history"] = [p for p in hist if (p.get("ts") or "") >= cutoff]


def _should_collect_heartbeat() -> bool:
    """Suppress heartbeat work outside active bot windows.

    Markets closed (weekends, holidays, after XETRA + US close, before
    morning prep) = no live quotes to collect, no trades being managed,
    nothing meaningful to write. Earlier behaviour wrote a stale
    snapshot 1×/min through the night and on weekends, burning disk
    I/O + bloating equity_history with hundreds of duplicate points per
    day. Dashboard now legitimately shows "stale" outside trading hours,
    which is accurate — the bot really is idle.

    Active windows kept on:
    - XETRA / US trading hours (is_market_hours)
    - Morning prep (08:00 CET) — bot is doing the daily Sonnet call
    - EOD summary (22:10-22:25 CET) — bot is computing the daily digest
    - Weekend summary (Sat/Sun 10:00-10:15 CET)
    - Weekend news scan (Sun 18:00-22:00 CET)
    """
    if is_market_hours():
        return True
    return (
        is_morning_prep_time()
        or is_eod_summary_time()
        or is_weekend_summary_time()
        or is_weekend_news_window()
    )


def _persist_heartbeat(state: AppState, now: datetime) -> None:
    """Telemetry snapshot for web dashboard. Throttled to ≤1×/min.
    Heartbeat dict goes to state/bot.db (runtime kv_state, own lock).
    Trade MAE/MFE + equity-history stay in portfolio.json (positional state)."""
    if (time.monotonic() - state.last_heartbeat_write) < HEARTBEAT_WRITE_INTERVAL_SEC:
        return
    if not _should_collect_heartbeat():
        return
    try:
        from core.portfolio.runtime_store import update_runtime
        try:
            live_prices, live_quotes = _collect_live_quotes(load_portfolio())
        except Exception:
            logger.exception("Live-price snapshot failed (heartbeat)")
            live_prices, live_quotes = {}, {}

        # Heartbeat → runtime.json (separate lock, no contention with trades)
        update_runtime({
            "heartbeat": {
                "last_tick": now.strftime("%Y-%m-%d %H:%M:%S"),
                "market_hours": is_market_hours(),
                "api_calls_today": get_daily_usage(),
                "api_cap": config.MAX_ANALYSES_PER_DAY,
                "prices": live_prices,
                "live_quotes": live_quotes,
            },
        })

        # Trade-state mutations (MAE/MFE/max_r_open + equity_history) stay
        # under portfolio_lock — those are positional invariants.
        with portfolio_lock:
            pf = load_portfolio()
            unrealized_eur = ratchet_open_trade_extremes(
                pf.get("open_trades", []) or [], live_prices,
            )
            if live_prices:
                _append_equity_point(pf, unrealized_eur, now)
            save_portfolio(pf)
        state.last_heartbeat_write = time.monotonic()
    except Exception:
        logger.exception("Heartbeat persist failed")


# ---------------------------------------------------------------------------
# Loop dispatch
# ---------------------------------------------------------------------------

def _run_daily_windows() -> None:
    """Fire once-per-day services within their time windows. Each marks itself done."""
    if is_morning_prep_time():
        run_morning_prep()
    if is_xetra_open_check_time():
        run_opening_check("xetra")
    if is_us_open_check_time():
        run_opening_check("us")
    if is_eod_summary_time():
        run_eod_summary()
    if is_weekend_summary_time():
        run_weekend_summary()


def _run_monitoring_agents() -> None:
    """Fire any scheduled observability agents (bug-watcher, health-inspector,
    eod-postmortem, weekly-calibrator, backlog-keeper). Each is gated by an
    individual feature flag in config.flags; no-op when flags are off.
    Cheap when nothing's due (single time-window check)."""
    try:
        from agents._lib.dispatcher import tick as agents_tick

        def _notify(msg: str):
            try:
                send_alert("🤖 Monitoring-Agent", msg)
            except Exception:
                logger.exception("agent-alert send failed")

        agents_tick(notify=_notify)
    except Exception:
        logger.exception("monitoring-agents tick failed")


def _run_poll_cycle(state: AppState) -> None:
    """Every-15-min poll: auto-kill, price/event/news monitors, exit reminders, DD alerts.
    Weekend window only runs news-check (markets closed)."""
    if is_market_hours():
        try:
            shock = maybe_auto_kill()
            if shock:
                send_alert(
                    "🛑 AUTO-KILL aktiviert",
                    f"{shock['reason']}\n\nKill-Switch flipped automatisch. "
                    f"SL/TP-Monitoring läuft weiter. "
                    f"Manuell aufheben via `/resume`.",
                )
        except Exception:
            logger.exception("auto-kill check failed")

        run_price_check()
        run_event_check()
        run_news_check(state)
        run_berkshire_check(state)
        try:
            check_exit_reminders()
        except Exception:
            logger.exception("Exit-reminder check failed")
        check_equity_alerts()
    elif is_weekend_news_window():
        run_news_check(state)
        run_berkshire_check(state)


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def graceful_shutdown(signum, frame):
    logger.info("👋 Shutting down Trading Advisor...")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _check_sibling_instance() -> None:
    """Fail-fast if another `python main.py` is already running.

    Telegram only allows one polling connection per token; two instances
    cause `terminated by other getUpdates request` spam (incident 2026-05-25).
    Compare against this PID to avoid self-matches. Caller may set
    `TA_ALLOW_MULTI=1` to skip (used for dev runs).
    """
    if os.environ.get("TA_ALLOW_MULTI") == "1":
        return
    import subprocess
    own = os.getpid()
    try:
        out = subprocess.run(
            ["pgrep", "-f", "python.*main\\.py"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as e:
        logger.warning("sibling-check pgrep failed: %s — proceeding anyway", e)
        return
    pids = [int(p) for p in out.stdout.split() if p.strip().isdigit() and int(p) != own]
    if pids:
        logger.error(
            "🛑 sibling main.py instances detected: %s — refusing to start (set TA_ALLOW_MULTI=1 to override)",
            pids,
        )
        sys.exit(2)


def main() -> None:
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    _check_sibling_instance()

    logger.info("=" * 50)
    logger.info("💹 Event-Driven Trading Advisor")
    logger.info("=" * 50)
    logger.info("Kapital: €%s", config.BUDGET_EUR)
    logger.info("Watchlist: %s", ", ".join(config.WATCHLIST))
    logger.info("Rohstoffe: %s", ", ".join(config.COMMODITIES))
    logger.info("Price Check: Jede %d Min", config.PRICE_CHECK_INTERVAL_MINUTES)
    logger.info("Morning Prep: %d:00 Uhr", config.MORNING_PREP_HOUR)
    logger.info(
        "Opening Checks: XETRA %d:%02d / US %d:%02d",
        config.XETRA_OPEN_HOUR, config.XETRA_OPEN_MINUTE,
        config.US_OPEN_HOUR, config.US_OPEN_MINUTE,
    )
    logger.info("Weekend News Scan: Sonntag 18-22 CET (geo-news catch-up)")
    logger.info("=" * 50)

    # Enable gate_log writes (off by default so test runs don't pollute
    # production gate_blocks.jsonl with synthetic recs — incident
    # 2026-05-23). Must be set before any handler imports run_entry_gates.
    os.environ.setdefault("TA_GATE_LOG_ENABLED", "1")
    # Same pattern for agent_runs.db (test-pollution fix 2026-05-25).
    os.environ.setdefault("TA_AGENT_RUNS_LOG_ENABLED", "1")

    from core.db import init_schema
    init_schema()
    startup_cleanup()
    start_listener_thread()

    state = AppState()
    threading.Thread(
        target=heartbeat_watchdog, args=(state.heartbeat,),
        daemon=True, name="heartbeat-watchdog",
    ).start()

    if is_morning_prep_time() or (is_market_hours() and not morning_prep_done_today()):
        run_morning_prep()

    logger.info("⏰ Monitoring aktiv. Ctrl+C zum Stoppen.")

    last_check = datetime.now()
    check_interval = config.PRICE_CHECK_INTERVAL_MINUTES * 60

    while True:
        state.tick()
        now = datetime.now()

        _persist_heartbeat(state, now)
        try:
            drain_web_commands()
        except Exception:
            logger.exception("web command drain failed")
        _run_daily_windows()
        _run_monitoring_agents()

        if (now - last_check).total_seconds() >= check_interval:
            _run_poll_cycle(state)
            last_check = now

        time.sleep(10)


if __name__ == "__main__":
    main()
