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
from core.livefeed import live_quote_for_ticker
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
from services.news_monitor import run_news_check
from services.opening_check import run_opening_check
from services.price_monitor import run_price_check
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


def _persist_heartbeat(state: AppState, now: datetime) -> None:
    """Write live quotes + equity snapshot to portfolio.json. Throttled."""
    if (time.monotonic() - state.last_heartbeat_write) < HEARTBEAT_WRITE_INTERVAL_SEC:
        return
    try:
        live_prices: dict[str, float] = {}
        live_quotes: dict[str, dict] = {}
        try:
            pf_snap = load_portfolio()
            tickers = list({
                *(t["ticker"] for t in pf_snap.get("open_trades", []) or [] if t.get("ticker")),
                *(w["ticker"] for w in pf_snap.get("watch_levels", []) or [] if w.get("ticker")),
            })
            # Heartbeat fast-path: skip yfinance, scrape ls-tc direct.
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
        except Exception:
            logger.exception("Live-price snapshot failed (heartbeat)")

        with portfolio_lock:
            pf = load_portfolio()
            pf["heartbeat"] = {
                "last_tick": now.strftime("%Y-%m-%d %H:%M:%S"),
                "market_hours": is_market_hours(),
                "api_calls_today": get_daily_usage(),
                "api_cap": config.MAX_ANALYSES_PER_DAY,
                "prices": live_prices,
                "live_quotes": live_quotes,
            }

            # Ratchet MAE/MFE + max_r_open on open trades.
            unrealized_eur = 0.0
            for ot in pf.get("open_trades", []) or []:
                tk = (ot.get("ticker") or "").upper()
                live = live_prices.get(tk)
                if not isinstance(live, (int, float)) or live <= 0:
                    continue
                entry = float(ot.get("entry_price") or 0)
                shares = float(ot.get("shares") or 0)
                if entry <= 0:
                    continue
                if live < ot.get("mae", entry):
                    ot["mae"] = round(live, 4)
                if live > ot.get("mfe", entry):
                    ot["mfe"] = round(live, 4)
                irs = ot.get("initial_risk_per_share")
                if isinstance(irs, (int, float)) and irs > 0:
                    r_now = (live - entry) / irs
                    if r_now > (ot.get("max_r_open", 0.0) or 0.0):
                        ot["max_r_open"] = round(r_now, 3)
                if shares > 0:
                    unrealized_eur += (live - entry) * shares

            # Append intraday equity-curve point. Pruned to 14d rolling.
            if live_prices:
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

            save_portfolio(pf)
        state.last_heartbeat_write = time.monotonic()
    except Exception:
        logger.exception("Heartbeat persist failed")


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def graceful_shutdown(signum, frame):
    logger.info("👋 Shutting down Trading Advisor...")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

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

    startup_cleanup()
    start_listener_thread()

    state = AppState()
    threading.Thread(
        target=heartbeat_watchdog, args=(state.heartbeat,),
        daemon=True, name="heartbeat-watchdog",
    ).start()

    # Catch-up morning prep on boot.
    if is_morning_prep_time() or (is_market_hours() and not morning_prep_done_today()):
        run_morning_prep()

    logger.info("⏰ Monitoring aktiv. Ctrl+C zum Stoppen.")

    last_check = datetime.now()
    check_interval = config.PRICE_CHECK_INTERVAL_MINUTES * 60

    while True:
        state.tick()
        now = datetime.now()

        _persist_heartbeat(state, now)

        # Daily / once-per-day windows (each marks itself done).
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

        # Every-15-min poll cycle.
        if (now - last_check).total_seconds() >= check_interval:
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
                try:
                    check_exit_reminders()
                except Exception:
                    logger.exception("Exit-reminder check failed")
                check_equity_alerts()
            elif is_weekend_news_window():
                run_news_check(state)

            last_check = now

        time.sleep(10)


if __name__ == "__main__":
    main()
