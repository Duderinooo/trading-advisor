#!/usr/bin/env python3
"""
Event-Driven Trading Advisor Bot
Monitors markets and triggers analysis when important events happen.
"""

import os
import re
import time
import logging
import signal
import sys
import threading
from datetime import datetime, date, timedelta
from logging.handlers import RotatingFileHandler

import anthropic
import config
from core import (
    analyze_portfolio,
    check_price_alerts,
    check_stop_loss_take_profit,
    detect_events,
    should_analyze_events,
    load_portfolio,
    save_portfolio,
    get_earnings_warnings,
    get_dividend_warnings,
    check_news_events,
    kill_switch_active,
    portfolio_lock,
    get_market_data,
    maybe_auto_kill,
)
from memory import log_trade, MEMPALACE_AVAILABLE
from notifier import send_notification, send_daily_summary, send_alert, send_actionable, _word_truncate
from telegram_listener import start_listener_thread


_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Root logger → rotating file. No StreamHandler: launchd's bot.err only catches
# pre-logging-init crashes (small) instead of the ~MB/day yfinance noise we saw before.
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
for _h in list(_root_logger.handlers):
    _root_logger.removeHandler(_h)
_file_handler = RotatingFileHandler(_LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
_root_logger.addHandler(_file_handler)

# Mute noisy 3rd-party loggers (httpx logs every Telegram poll at INFO).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# yfinance ERROR-spam: ETFs/Commodities ohne Fundamentals 404'en erwartbar,
# transiente "possibly delisted" sind Yahoo-API-Hickups (vergehen von selbst).
# Beide Cases werden im market_data.py per try/except behandelt — Logger-Noise unnötig.
class _YFinanceNoiseFilter(logging.Filter):
    _SUPPRESS = ("possibly delisted", "No fundamentals data found")
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(s in msg for s in self._SUPPRESS)
logging.getLogger("yfinance").addFilter(_YFinanceNoiseFilter())

logger = logging.getLogger("trading_advisor")


# Heartbeat: main-loop tick timestamp. Watchdog thread alerts via Telegram if
# the loop is silent for >30 min during market hours. launchd KeepAlive only
# covers process death — this catches hangs (deadlock, stuck network call).
_HEARTBEAT_STALE_SEC = 30 * 60
_heartbeat = [time.monotonic()]


def _heartbeat_watchdog():
    alerted = False
    while True:
        try:
            time.sleep(60)
            delta = time.monotonic() - _heartbeat[0]
            if delta > _HEARTBEAT_STALE_SEC:
                if not alerted:
                    try:
                        send_alert(
                            "Bot Hang",
                            f"Main loop stale {int(delta / 60)} min "
                            f"(letzte Iteration vor {int(delta)}s). "
                            f"launchd restartet bei Crash, aber Prozess lebt → manuell prüfen.",
                        )
                    except Exception as exc:
                        logger.error("Heartbeat alert failed: %s", exc)
                    alerted = True
            else:
                alerted = False
        except Exception as exc:
            logger.error("Watchdog tick failed: %s", exc)


# Strict grammar: first non-empty line MUST start with an action verb followed by
# a ticker and a separator. Markdown preamble or "Internal Analysis" blocks fail
# this match silently. Synonyms (BUY/KAUFEN/SELL/VERKAUFEN/CLOSE) get normalized
# to canonical actions for the schema validator.
_ACTION_GRAMMAR = re.compile(
    r"^(?P<action>ENTRY|EXIT|ADD|REDUCE|CLOSE|BUY|SELL|KAUFEN|VERKAUFEN)"
    r"\s*[:|]\s*(?P<ticker>[A-Z0-9.\-\^]{1,12})\s*[|@]\s*(?P<rest>.+)",
    re.IGNORECASE,
)

_ACTION_NORMALIZE = {
    "BUY": "ENTRY", "KAUFEN": "ENTRY",
    "SELL": "EXIT", "VERKAUFEN": "EXIT", "CLOSE": "EXIT",
}


# Drawdown-cross alert thresholds (% from peak).
_DD_ALERT_THRESHOLDS = (5.0, 10.0, 15.0, 20.0)


def _check_equity_alerts():
    """Drawdown-threshold-cross + new-equity-ATH alerts. State persisted in
    portfolio.json so each threshold fires exactly once per cycle (re-arms on
    return-to-peak). Live equity = realized + unrealized using heartbeat quotes."""
    try:
        with portfolio_lock:
            pf = load_portfolio()
            cap = float(pf.get("total_capital_eur") or 0)
            if cap <= 0:
                return
            realized = cap
            for t in pf.get("closed_trades", []):
                realized += float(t.get("pnl_eur") or 0)
            for m in pf.get("cash_movements", []) or []:
                realized += float(m.get("amount") or 0)
            quotes = (pf.get("heartbeat") or {}).get("live_quotes") or {}
            prices = (pf.get("heartbeat") or {}).get("prices") or {}
            open_trades = pf.get("open_trades", []) or []
            # If LS-TC heartbeat empty (failed scrape, first tick after boot),
            # fall back to yfinance so unrealized isn't silently 0 → false DD alerts.
            missing = [
                (t.get("ticker") or "").upper() for t in open_trades
                if (t.get("ticker") or "").upper()
                and not ((quotes.get((t.get("ticker") or "").upper()) or {}).get("price")
                         or prices.get((t.get("ticker") or "").upper()))
            ]
            yf_fallback: dict[str, float] = {}
            if missing:
                try:
                    snaps = get_market_data(missing)
                    for _tk, _snap in (snaps or {}).items():
                        _p = _snap.get("price") if isinstance(_snap, dict) else None
                        if isinstance(_p, (int, float)):
                            yf_fallback[(_tk or "").upper()] = float(_p)
                except Exception:
                    logger.exception("Equity-alert yfinance fallback failed")
            unrealized = 0.0
            for t in open_trades:
                tk = (t.get("ticker") or "").upper()
                live = (
                    (quotes.get(tk) or {}).get("price")
                    or prices.get(tk)
                    or yf_fallback.get(tk)
                )
                entry = float(t.get("entry_price") or 0)
                shares = float(t.get("shares") or 0)
                if isinstance(live, (int, float)) and entry > 0 and shares > 0:
                    unrealized += (live - entry) * shares
            equity = realized + unrealized

            ath = float(pf.get("equity_ath") or cap)
            dd_alerts = set(pf.get("dd_alerts_triggered") or [])

            dirty = False

            # New ATH (only after at least one realized close — avoid intraday spam
            # on the very first up-tick before any trade closed).
            if equity > ath + 0.01 and len(pf.get("closed_trades", []) or []) >= 1:
                send_alert(
                    "📈 EQUITY NEW ATH",
                    f"Live €{equity:.2f} (alt-ATH €{ath:.2f}, "
                    f"+{(equity / cap - 1) * 100:+.2f}% vs Start).",
                )
                pf["equity_ath"] = round(equity, 2)
                ath = equity
                # Reset DD-alert state when re-arming above peak.
                dd_alerts = set()
                dirty = True

            # Drawdown-cross: each threshold fires once per peak.
            dd_pct = (ath - equity) / ath * 100 if ath > 0 else 0
            for thr in _DD_ALERT_THRESHOLDS:
                if dd_pct >= thr and thr not in dd_alerts:
                    send_alert(
                        f"⚠️ DRAWDOWN −{thr:.0f}% Cross",
                        f"Live €{equity:.2f} vs Peak €{ath:.2f} → −{dd_pct:.2f}%.\n"
                        f"Realized €{realized:.2f} · Unrealized €{unrealized:+.2f}.",
                    )
                    dd_alerts.add(thr)
                    dirty = True

            if dirty:
                pf["equity_ath"] = round(ath, 2)
                pf["dd_alerts_triggered"] = sorted(dd_alerts)
                save_portfolio(pf)
    except Exception:
        logger.exception("Equity alerts check failed")


def _enrich_extras_for_add(parsed: dict, base_extras: dict, portfolio: dict | None = None) -> dict:
    """When Claude emits 'ADD: ...' as TEXT (e.g. tool-call gates blocked or Sonnet-lazy),
    look up the open trade and surface Bestand/SL so user has actionable context.
    Without this, ADD text-mode shows just '🎯 ADD | TICKER' with no size hint.

    Also: inject TR-WKN for all actions if the ticker maps in `config.TR_WKN_MAP`.
    User trades on TR via WKN, not yfinance ticker — alert must surface what to type
    in the broker app.

    Pass `portfolio` if already loaded (e.g. by _parse_actionable) to avoid a duplicate
    file read on the hot path."""
    enriched = dict(base_extras)

    # WKN-injection for any action (ENTRY/EXIT/ADD/REDUCE) — only present when
    # yfinance-ticker differs from TR-tradable WKN. Surfaced first so user sees it
    # at a glance.
    ticker = (parsed or {}).get("ticker", "").upper()
    wkn = config.TR_WKN_MAP.get(ticker)
    if wkn:
        enriched = {"TR-WKN": wkn, **enriched}

    if (parsed or {}).get("action") != "ADD":
        return enriched
    if portfolio is None:
        portfolio = load_portfolio()
    open_trade = next(
        (t for t in portfolio.get("open_trades", [])
         if (t.get("ticker") or "").upper() == ticker),
        None,
    )
    if not open_trade:
        return enriched
    enriched["Bestand"] = (
        f"€{float(open_trade.get('size_eur') or 0):.0f} "
        f"@ €{float(open_trade.get('entry_price') or 0):.2f}"
    )
    sl = open_trade.get("stop_loss")
    if sl:
        enriched["SL"] = f"€{float(sl):.2f}"
    enriched["Hinweis"] = "Size+Preis via /add command bestimmen"
    return enriched


def _parse_actionable(analysis: str, portfolio: dict | None = None) -> dict | None:
    """Parse Claude's verdict line into a structured dict, or None if non-actionable.

    Returns {"action": ENTRY|EXIT|ADD|REDUCE, "ticker": ..., "reason": ...}
    Drops:
    - PASS/HALTEN/HOLD verdicts.
    - Grammar misses (markdown preamble, multi-line analysis, etc.).
    - EXIT/ADD/REDUCE on tickers without an open position (Bug 2026-05-02:
      WATCH_INVALIDATED on NVD.DE → Haiku emitted 'EXIT: NVD.DE' text →
      Telegram even though no NVDA position existed).
    - ENTRY/EXIT/ADD that the analyzer.py tool path *just persisted* as a
      pending_recommendation — text dup-message suppressed (Bug 2026-05-02:
      SIE.DE rec sent both via recommend_entry tool AND text-parse).

    Caller may pass a pre-loaded `portfolio` to skip a redundant file read.
    """
    if not analysis:
        return None
    first = analysis.strip().split("\n", 1)[0].strip().lstrip("*•` ")
    if first.upper().startswith(("PASS", "HALTEN", "HOLD")):
        return None
    m = _ACTION_GRAMMAR.match(first)
    if not m:
        return None
    raw = m.group("action").upper()
    action = _ACTION_NORMALIZE.get(raw, raw)
    ticker = m.group("ticker").upper()

    pf = portfolio if portfolio is not None else load_portfolio()
    open_tickers = {(t.get("ticker") or "").upper() for t in pf.get("open_trades", [])}

    if action in ("EXIT", "ADD", "REDUCE") and ticker not in open_tickers:
        logger.info(
            "Text-parse %s suppressed: %s has no open position", action, ticker,
        )
        return None

    # Pending check serves both dedup AND orphan-detection:
    # - Recent pending for ticker → structured rec already went out (dup) → suppress.
    # - For ENTRY without any recent pending → tool path didn't persist (red-team
    #   killed it, slippage gate, etc.). Text-parse would send a /confirm message
    #   user can't actually act on (Bug 2026-04-30: 3OIL.MI ENTRY text emitted
    #   even though red-team blocked the tool, leaving user with no /confirm
    #   target). Suppress.
    now = datetime.now()
    has_recent_pending = False
    for rec in pf.get("pending_recommendations", []) or []:
        if (rec.get("ticker") or "").upper() != ticker:
            continue
        ts = rec.get("timestamp")
        if not ts:
            continue
        try:
            rec_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if (now - rec_dt) <= timedelta(minutes=5):
            has_recent_pending = True
            break
    if has_recent_pending:
        logger.info(
            "Text-parse %s suppressed: structured rec for %s already pending",
            action, ticker,
        )
        return None
    if action == "ENTRY":
        logger.info(
            "Text-parse ENTRY suppressed: %s has no pending rec — "
            "tool path likely blocked by gates (red-team / risk-halt)", ticker,
        )
        return None

    return {
        "action": action,
        "ticker": ticker,
        "reason": m.group("rest").strip()[:200],
    }


def _morning_prep_done_today() -> bool:
    """Check if morning prep already ran today (persisted in portfolio.json)."""
    portfolio = load_portfolio()
    return portfolio.get("last_morning_prep_date") == str(date.today())


def _mark_morning_prep_done():
    portfolio = load_portfolio()
    portfolio["last_morning_prep_date"] = str(date.today())
    save_portfolio(portfolio)


def _opening_check_done_today(market: str) -> bool:
    portfolio = load_portfolio()
    return portfolio.get(f"last_{market}_open_check_date") == str(date.today())


def _mark_opening_check_done(market: str):
    portfolio = load_portfolio()
    portfolio[f"last_{market}_open_check_date"] = str(date.today())
    save_portfolio(portfolio)


# Anthropic 529 / network blips are recurring at EU peak (Xetra open ~09:05 —
# see 2026-05-19/-20 incidents). Once-per-day checks treat them as terminal,
# killing the day. The transient bucket below lets the natural 15-min loop tick
# retry instead, capped so a multi-hour outage doesn't loop forever.
_TRANSIENT_RETRY_CAP = 3
_TRANSIENT_RETRY_STATUSES = frozenset({429, 502, 503, 504, 529})


def _is_transient(exc: BaseException) -> bool:
    """True if `exc` is a transient API / network error worth retrying."""
    if isinstance(exc, anthropic.APIStatusError):
        return getattr(exc, "status_code", None) in _TRANSIENT_RETRY_STATUSES
    return isinstance(exc, (
        anthropic.APIConnectionError, anthropic.APITimeoutError,
        ConnectionError, TimeoutError, OSError,
    ))


def _transient_retry_inc(key: str) -> int:
    """Bump today's transient-retry counter for `key`; return the new count.
    Counter is per-day — stale entries from earlier dates are reset on bump."""
    pf = load_portfolio()
    bucket = pf.setdefault("transient_retries", {})
    today = str(date.today())
    entry = bucket.get(key) or {}
    if entry.get("date") != today:
        entry = {"date": today, "count": 0}
    entry["count"] += 1
    bucket[key] = entry
    save_portfolio(pf)
    return entry["count"]


def _transient_retry_reset(key: str) -> None:
    """Clear today's transient counter on a successful run."""
    pf = load_portfolio()
    bucket = pf.get("transient_retries") or {}
    if key in bucket:
        bucket.pop(key)
        save_portfolio(pf)


def _is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in config.XETRA_HOLIDAYS


def is_market_hours() -> bool:
    """True if within XETRA trading hours (weekday, not a holiday)."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return config.MARKET_OPEN_HOUR <= now.hour < config.MARKET_CLOSE_HOUR


def is_morning_prep_time() -> bool:
    """Check if it's time for morning prep (weekday, non-holiday)."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return now.hour == config.MORNING_PREP_HOUR and now.minute < 5


def is_xetra_open_check_time() -> bool:
    """5–15min window after XETRA Kassa open."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return (
        now.hour == config.XETRA_OPEN_HOUR
        and config.XETRA_OPEN_MINUTE <= now.minute < config.XETRA_OPEN_MINUTE + 10
    )


def is_weekend_news_window() -> bool:
    """Sunday 18:00-22:00 CET — pre-Monday geo-news catch-up window.
    Catches weekend events (Iran strike, OPEC surprise) early enough to queue
    a Monday-open entry. Single scan per hour is enough."""
    now = datetime.now()
    return now.weekday() == 6 and 18 <= now.hour < 22


def is_eod_summary_time() -> bool:
    """22:10–22:25 CET on trading days. After US close, before midnight rollover."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return now.hour == 22 and 10 <= now.minute < 25


def _eod_summary_done_today() -> bool:
    return load_portfolio().get("last_eod_summary_date") == str(date.today())


def _mark_eod_summary_done():
    portfolio = load_portfolio()
    portfolio["last_eod_summary_date"] = str(date.today())
    save_portfolio(portfolio)


def run_eod_summary():
    """One-shot EOD digest: realized P&L, open positions w/ unrealized, gates state."""
    if _eod_summary_done_today():
        return
    try:
        portfolio = load_portfolio()
        today = str(date.today())

        closed_today = [
            t for t in portfolio.get("closed_trades", [])
            if (t.get("exit_date") or "").startswith(today)
        ]
        realized_eur = sum(float(t.get("pnl_eur") or 0) for t in closed_today)
        wins_today = sum(1 for t in closed_today if (t.get("pnl_eur") or 0) > 0)

        open_trades = portfolio.get("open_trades", [])
        unrealized_eur = 0.0
        if open_trades:
            try:
                live = get_market_data([t["ticker"] for t in open_trades])
                for t in open_trades:
                    price = (live.get(t["ticker"]) or {}).get("price") if isinstance(live.get(t["ticker"]), dict) else None
                    entry = float(t.get("entry_price") or 0)
                    shares = float(t.get("shares") or 0)
                    if isinstance(price, (int, float)) and entry > 0:
                        unrealized_eur += (price - entry) * shares
            except Exception:
                logger.exception("EOD live-pull failed")

        from core import get_daily_usage
        calls_today = get_daily_usage()
        ks_state = "🛑 AKTIV" if kill_switch_active(portfolio) else "✅ aus"
        dd_state = "🚫 DD-LATCH" if portfolio.get("dd_halt_active") else "—"

        cash = float(portfolio.get("cash_eur") or 0)
        starting = float(portfolio.get("total_capital_eur") or config.BUDGET_EUR)
        equity_realized = starting + sum(float(t.get("pnl_eur") or 0) for t in portfolio.get("closed_trades", []))
        equity_total = equity_realized + unrealized_eur

        msg = (
            f"📊 *EOD {today}*\n\n"
            f"Realized today: €{realized_eur:+.2f} ({len(closed_today)} closed, {wins_today}W)\n"
            f"Unrealized: €{unrealized_eur:+.2f} ({len(open_trades)} offen)\n"
            f"Cash: €{cash:.2f} | Equity: €{equity_total:.2f} (Start €{starting:.2f})\n\n"
            f"Claude calls: {calls_today}/{config.MAX_ANALYSES_PER_DAY}\n"
            f"Kill-Switch: {ks_state} | DD-Halt: {dd_state}"
        )
        send_notification(msg)
        _mark_eod_summary_done()
        logger.info("✅ EOD summary sent")
    except Exception:
        logger.exception("EOD summary failed")


def is_weekend_summary_time() -> bool:
    """Sat 10:00–10:15 CET OR Sun 10:00–10:15 CET. Two-shot weekend recap window."""
    now = datetime.now()
    if now.weekday() not in (5, 6):
        return False
    return now.hour == 10 and now.minute < 15


def _weekend_summary_done_today() -> bool:
    return load_portfolio().get("last_weekend_summary_date") == str(date.today())


def _mark_weekend_summary_done():
    portfolio = load_portfolio()
    portfolio["last_weekend_summary_date"] = str(date.today())
    save_portfolio(portfolio)


def run_weekend_summary():
    """Sat/Sun 10:00 CET digest. Deterministic, no Claude.

    Why: weekend has no Morning Brief (not a trading day), but user wants weekly
    recap + alive-ping. Surfaces last-week P&L, open exposure, mistake-class
    drift, upcoming earnings + macro for next week.
    """
    if _weekend_summary_done_today():
        return
    try:
        from core import (
            compute_portfolio_heat, compute_hit_stats, compute_equity_stats,
            get_earnings_warnings, get_daily_usage,
        )
        from macro import today_events as _macro_today

        portfolio = load_portfolio()
        today = date.today()

        # Last 7 days realized P&L
        cutoff = today - timedelta(days=7)
        closed_recent = [
            t for t in portfolio.get("closed_trades", [])
            if (t.get("exit_date") or "") >= str(cutoff)
        ]
        realized_eur = sum(float(t.get("pnl_eur") or 0) for t in closed_recent)
        wins = sum(1 for t in closed_recent if (t.get("pnl_eur") or 0) > 0)
        losses = len(closed_recent) - wins

        # Open positions + unrealized
        open_trades = portfolio.get("open_trades", [])
        unrealized = 0.0
        if open_trades:
            try:
                live = get_market_data([t["ticker"] for t in open_trades])
                for t in open_trades:
                    snap = live.get(t["ticker"])
                    price = snap.get("price") if isinstance(snap, dict) else None
                    entry = float(t.get("entry_price") or 0)
                    shares = float(t.get("shares") or 0)
                    if isinstance(price, (int, float)) and entry > 0:
                        unrealized += (price - entry) * shares
            except Exception:
                logger.exception("Weekend live-pull failed")

        starting = float(portfolio.get("total_capital_eur") or config.BUDGET_EUR)
        equity_realized = (
            starting
            + sum(float(t.get("pnl_eur") or 0) for t in portfolio.get("closed_trades", []))
            + sum(float(m.get("amount") or 0) for m in portfolio.get("cash_movements", []))
        )
        equity_total = equity_realized + unrealized

        heat = compute_portfolio_heat(portfolio)
        eq = compute_equity_stats(
            portfolio.get("closed_trades", []),
            starting,
            portfolio.get("cash_movements", []),
        ) or {}
        stats = compute_hit_stats(portfolio.get("closed_trades", []), portfolio.get("cash_movements", []))

        # Mistake distribution (last 20 losses) — surfaces drift
        last_losses = [
            t for t in portfolio.get("closed_trades", [])
            if (t.get("pnl_pct") or 0) < 0
        ][-20:]
        mistake_line = ""
        if last_losses:
            cls_counts: dict[str, int] = {}
            for t in last_losses:
                c = t.get("mistake_class") or "untagged"
                cls_counts[c] = cls_counts.get(c, 0) + 1
            mistake_line = " | ".join(f"{c}={n}" for c, n in sorted(cls_counts.items(), key=lambda x: -x[1]))

        # Upcoming earnings next 7 days for open positions + watchlist
        candidates = list({t["ticker"] for t in open_trades} | set(config.WATCHLIST))
        upcoming_earn = get_earnings_warnings(candidates, days_ahead=7)
        earn_line = ""
        if upcoming_earn:
            earn_line = "\n".join(
                f"  • {w['ticker']}: T-{w['days_until']} ({w['earnings_date']})"
                for w in sorted(upcoming_earn, key=lambda x: x["days_until"])[:8]
            )

        # Today's macro events as a peek into next week
        macro = _macro_today()
        macro_line = ""
        if macro:
            macro_line = "\n".join(
                f"  • {m.get('time','?')} {m.get('country','')}: {m.get('event','?')}"
                for m in macro[:5]
            )

        ks_state = "🛑 AKTIV" if kill_switch_active(portfolio) else "✅ aus"
        dd_state = "🚫 DD-LATCH" if portfolio.get("dd_halt_active") else "—"

        # Last-week call count from api usage file isn't trivially weekly — show today only.
        calls_today = get_daily_usage()

        msg = (
            f"📅 *WEEKEND-RECAP {today}*\n\n"
            f"_Last 7d_: €{realized_eur:+.2f} ({len(closed_recent)} closed, {wins}W/{losses}L)\n"
            f"Unrealized: €{unrealized:+.2f} ({len(open_trades)} offen)\n"
            f"Equity: €{equity_total:.2f} | Cash: €{portfolio.get('cash_eur',0):.2f}\n"
            f"Max DD all-time: {eq.get('max_drawdown_pct',0):.1f}% | "
            f"Heat: €{heat['total_heat_eur']:.2f} ({heat['heat_pct']:.1f}%)\n"
            f"Kill-Switch: {ks_state} | DD-Halt: {dd_state} | Calls heute: {calls_today}\n"
        )
        if stats and stats.get("calibration"):
            cal = stats["calibration"]
            msg += (
                f"\n*Calibration*: Brier {cal['avg_brier']:.3f}, "
                f"p_pred {cal['avg_p_predicted']:.2f} vs actual {cal['actual_win_rate']:.2f}"
            )
            if cal.get("haircut"):
                msg += f" | Haircut aktiv: {cal['haircut']:+.2f}"
        if mistake_line:
            msg += f"\n*Mistakes (last 20 L)*: {mistake_line}"
        if earn_line:
            msg += f"\n\n*Earnings nächste 7d:*\n{earn_line}"
        if macro_line:
            msg += f"\n\n*Macro heute (Vorschau):*\n{macro_line}"

        send_daily_summary(msg)
        _mark_weekend_summary_done()
        logger.info("✅ Weekend summary sent")
    except Exception:
        logger.exception("Weekend summary failed")


def is_us_open_check_time() -> bool:
    """5–15min window after US market open (15:30 CET)."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return (
        now.hour == config.US_OPEN_HOUR
        and config.US_OPEN_MINUTE <= now.minute < config.US_OPEN_MINUTE + 10
    )


_EXIT_REMINDER_THRESHOLDS_MIN = {"now": 120, "today": 360, "eod": 60}


def _gap_min(last_at_str, now):
    """Minutes since last_at_str (formatted '%Y-%m-%d %H:%M'). None if unparseable."""
    if not last_at_str:
        return None
    try:
        return (now - datetime.strptime(last_at_str, "%Y-%m-%d %H:%M")).total_seconds() / 60.0
    except ValueError:
        return None


def _send_exit_reminder(rec: dict, age_min: float):
    """Send a Telegram exit reminder. After EXIT_AUTO_DROP_GAP_MIN ohne Action
    wird Rec verworfen — siehe _drop_exit_rec."""
    ticker = rec.get("ticker", "?")
    reason = (rec.get("reason") or "")[:120]
    urgency = (rec.get("urgency") or "today").lower()
    urg_emoji = {"now": "🚨", "today": "⚠️", "eod": "🕐"}.get(urgency, "⚠️")
    send_notification(
        f"⏰ *EXIT-REMINDER* | `{ticker}` | {urg_emoji} {urgency}\n"
        f"Vor {int(age_min)} Min empfohlen, noch nicht actioned.\n"
        f"Grund: {reason}\n"
        f"_Auf TR schließen + `/confirm` oder `/close {ticker} @PREIS`. "
        f"Ohne Action in {config.EXIT_AUTO_DROP_GAP_MIN}min wird Empfehlung verworfen._"
    )
    logger.warning("EXIT-reminder sent: %s urgency=%s age=%dmin",
                   ticker, urgency, int(age_min))


def _drop_exit_rec(rec: dict, portfolio: dict, now: datetime):
    """Auto-drop pending exit-rec ohne Action nach Reminder. Marks trade mit
    `exit_dropped_at` → analyzer-Cooldown suppresst neue Exit-Recs für
    EXIT_REC_COOLDOWN_MIN_AFTER_DROP min."""
    ticker = (rec.get("ticker") or "?").upper()
    for tr in portfolio.get("open_trades", []) or []:
        if (tr.get("ticker") or "").upper() == ticker:
            tr["exit_dropped_at"] = now.strftime("%Y-%m-%d %H:%M")
            break
    cooldown_h = config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP // 60
    send_notification(
        f"🗑️ *EXIT-Rec verworfen* | `{ticker}`\n"
        f"Nach Reminder keine Aktion. Bot generiert für {cooldown_h}h keine "
        f"neue Exit-Rec auf diesen Ticker."
    )
    logger.warning("EXIT-rec auto-dropped: %s after reminder (cooldown %dh)",
                   ticker, cooldown_h)


def _check_exit_reminders():
    """Reminder + Auto-Drop für pending EXIT-recs. Bot hat keine Broker-API; bei
    User-Ignore rottet Position. State-Machine pro Rec:
      reminder_sent=False → Threshold hit → Reminder, last_reminder_at=now
      reminder_sent=True  → AUTO_DROP_GAP since last_reminder_at → drop + cooldown
    Ein Reminder reicht — User-Feedback 2026-05-04.
    """
    if not is_market_hours():
        return
    with portfolio_lock:
        portfolio = load_portfolio()
        pending = portfolio.get("pending_recommendations", []) or []
        if not pending:
            return
        now = datetime.now()
        dirty = False
        kept = []
        for rec in pending:
            if rec.get("kind") != "exit":
                kept.append(rec)
                continue
            ts = rec.get("timestamp")
            if not ts:
                kept.append(rec)
                continue
            try:
                rec_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
            except ValueError:
                kept.append(rec)
                continue

            urgency = (rec.get("urgency") or "today").lower()
            threshold = _EXIT_REMINDER_THRESHOLDS_MIN.get(urgency, 360)
            age_min = (now - rec_dt).total_seconds() / 60.0

            # Threshold hit?
            if urgency == "eod":
                # 1h before XETRA close (17:30)
                close_min = 17 * 60 + 30
                cutoff_min = close_min - threshold
                threshold_hit = (now.hour * 60 + now.minute) >= cutoff_min
            else:
                threshold_hit = age_min >= threshold

            if not threshold_hit:
                kept.append(rec)
                continue

            sent = bool(rec.get("reminder_sent"))
            last_at = rec.get("last_reminder_at")

            if not sent:
                _send_exit_reminder(rec, age_min)
                rec["reminder_sent"] = True
                rec["last_reminder_at"] = now.strftime("%Y-%m-%d %H:%M")
                dirty = True
                kept.append(rec)
            else:
                gap = _gap_min(last_at, now)
                if gap is not None and gap >= config.EXIT_AUTO_DROP_GAP_MIN:
                    _drop_exit_rec(rec, portfolio, now)
                    dirty = True
                    # NICHT zu kept → fällt aus pending raus
                else:
                    kept.append(rec)

        if dirty:
            portfolio["pending_recommendations"] = kept
            save_portfolio(portfolio)


def _check_stale_theses():
    """Alert on positions held longer than `hold_days_max` from rec.
    Why: thesis horizon is 3-7d for swing; positions drifting beyond signal a hope-trade.
    Re-fires once per day at most via stale_alerted_date persistence."""
    today = str(date.today())
    with portfolio_lock:
        portfolio = load_portfolio()
        dirty = False
        for trade in portfolio.get("open_trades", []):
            hold_max = trade.get("hold_days_max")
            entry_date = trade.get("entry_date")
            if not isinstance(hold_max, (int, float)) or not entry_date:
                continue
            try:
                entry_d = datetime.strptime(entry_date[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            days_held = (date.today() - entry_d).days
            if days_held < hold_max:
                continue
            if trade.get("stale_alerted_date") == today:
                continue
            ticker = trade.get("ticker", "?")
            send_alert(
                f"🕒 STALE THESIS: {ticker}",
                f"Held {days_held}d > Thesis-Horizon {int(hold_max)}d.\n"
                f"Thesis: _{trade.get('thesis','—')}_\n"
                f"Entscheide: schließen, re-analyzen, oder Trailing-Stop straffen.",
            )
            logger.warning("Stale thesis alert: %s held %dd > %d", ticker, days_held, hold_max)
            trade["stale_alerted_date"] = today
            dirty = True
        if dirty:
            save_portfolio(portfolio)


def run_morning_prep(force: bool = False):
    """Run morning analysis. force=True overrides date-dedup + kill-switch
    + forced-call cooldown (manual /morning trigger)."""
    if not force and _morning_prep_done_today():
        return
    if not force and kill_switch_active(load_portfolio()):
        logger.info("Morning prep skipped: kill-switch active")
        _mark_morning_prep_done()
        return

    logger.info("☀️ Running morning prep%s...", " (forced)" if force else "")

    try:
        _check_stale_theses()
    except Exception:
        logger.exception("Stale-thesis check failed")

    # Direct earnings alert for open positions — fires before Claude, guaranteed delivery
    try:
        open_tickers = [t["ticker"] for t in load_portfolio().get("open_trades", [])]
        if open_tickers:
            for w in get_earnings_warnings(open_tickers, days_ahead=2):
                if w["days_until"] <= config.EARNINGS_CLOSE_DAYS:
                    send_alert(
                        f"🚨 EARNINGS MORGEN: {w['ticker']} — CLOSE EMPFOHLEN",
                        f"Earnings in *{w['days_until']} Tag(en)* ({w['earnings_date']})\n"
                        f"⚠️ Position JETZT schließen oder auf 25% Size reduzieren.\n"
                        f"Grund: Overnight-Gap-Risiko (IV crush + unbekannte Richtung).",
                    )
                    logger.warning("Earnings CLOSE recommended: %s in %d days", w["ticker"], w["days_until"])
                else:
                    send_alert(
                        f"⚠️ EARNINGS: {w['ticker']}",
                        f"Earnings in *{w['days_until']} Tag(en)* ({w['earnings_date']})\n"
                        f"Offene Position! Vor Earnings schließen oder Size reduzieren.",
                    )
                    logger.warning("Earnings warning sent: %s in %d days", w["ticker"], w["days_until"])
    except Exception:
        logger.exception("Earnings pre-check failed")

    # Ex-dividend pre-check: only notify when the mechanical drop threatens the SL.
    # Plain "ex-div anstehend" is info-noise — actionable cases require user to lower
    # SL the night before; non-threats are logged only.
    try:
        open_trades = load_portfolio().get("open_trades", [])
        if open_trades:
            by_ticker = {t["ticker"]: t for t in open_trades}
            warns = get_dividend_warnings(list(by_ticker.keys()), days_ahead=config.DIVIDEND_WARN_DAYS)
            for w in warns:
                trade = by_ticker.get(w["ticker"])
                if not trade:
                    continue
                entry = trade.get("entry_price")
                sl = trade.get("stop_loss")
                div = w["expected_div"]
                if not (entry and sl and entry > sl):
                    logger.info("Dividend info-only (no SL): %s ex=%s div=%.2f", w["ticker"], w["ex_date"], div)
                    continue
                sl_distance = entry - sl
                drop_share = div / sl_distance if sl_distance > 0 else 0
                if drop_share < 0.5:
                    logger.info(
                        "Dividend info-only (drop_share=%.2f<0.5): %s ex=%s div=%.2f",
                        drop_share, w["ticker"], w["ex_date"], div,
                    )
                    continue
                suggested_sl = round(sl - div, 2)
                msg = (
                    f"Ex-Div in *{w['days_until']} Tag(en)* ({w['ex_date']})\n"
                    f"Erwartete Ausschüttung: ~€{div:.2f}/Aktie\n"
                    f"SL-Distance: €{sl_distance:.2f} | Ex-Div-Drop frisst {drop_share*100:.0f}% davon\n"
                    f"⚠️ Mechanischer Drop kann SL triggern.\n"
                    f"Vorschlag: SL temporär auf €{suggested_sl:.2f} senken (heute Abend), "
                    f"nach Ex-Div ({w['ex_date']}) zurücksetzen."
                )
                send_alert(f"💸 EX-DIV WARNUNG: {w['ticker']} — SL-Risiko", msg)
                logger.warning(
                    "Dividend SL-threat alert: %s ex=%s div=%.2f drop_share=%.2f",
                    w["ticker"], w["ex_date"], div, drop_share,
                )
    except Exception:
        logger.exception("Dividend pre-check failed")

    try:
        # force=True (manual /morning) bypasses both forced-call cooldown
        # AND date-dedup. Auto-runs (force=False) keep cooldown to protect daily cap.
        analysis = analyze_portfolio(
            mode="morning", force=force, bypass_cooldown=force
        )
        # Output-Sanitizer (2026-05-22): Sonnet schrieb v7-Morning eine Brain-Dump-
        # Essay statt 3-Section-Format. Strippe alles vor der ersten validen Zeile
        # (Markt-Regime / TICKER | Entry / Keine sauberen / TICKER | €).
        if analysis and not analysis.startswith("⚠️ Analysis skipped"):
            import re
            lines = analysis.splitlines()
            valid_start_idx = None
            valid_patterns = (
                "Markt-Regime:",
                "Keine sauberen Limit-Buy-Kandidaten",
                "Keine Setups heute",
            )
            entry_line_re = re.compile(r"^[A-Z0-9]+\.?[A-Z]{0,3}\s+\|\s+(Entry|€)")
            for i, line in enumerate(lines):
                stripped_line = line.strip()
                if any(stripped_line.startswith(p) for p in valid_patterns):
                    valid_start_idx = i
                    break
                if entry_line_re.match(stripped_line):
                    valid_start_idx = i
                    break
            if valid_start_idx is not None and valid_start_idx > 0:
                prefix_strip_len = sum(len(l) + 1 for l in lines[:valid_start_idx])
                logger.warning(
                    "Morning output sanitized: stripped %d chars / %d lines of pre-amble",
                    prefix_strip_len, valid_start_idx,
                )
                analysis = "\n".join(lines[valid_start_idx:])
            elif valid_start_idx is None and len(analysis) > 200:
                # No valid line found — Sonnet drifted hard. Replace with status.
                logger.error(
                    "Morning output drift: no valid Pflicht-Zeile found in %d-char response",
                    len(analysis),
                )
                analysis = "⚠️ Sonnet-Drift: Output ohne gültige Pflicht-Zeile. Bot-Tool-Calls liefen ggf. trotzdem. Check /pending."

        stripped = (analysis or "").strip().lower()
        if analysis and analysis.startswith("⚠️ Analysis skipped"):
            # Cooldown/cap blocked the call → don't mark done, don't notify (retry later).
            logger.info("Morning prep deferred: %s", analysis)
            return
        if "(keine text-analyse)" in stripped or not stripped:
            # Sonnet emitted only tool-calls (set_watch_levels / recommend_entry) without
            # text. Build a deterministic status heartbeat so user has explicit feedback
            # whether bot ran + 0-watchlevel-day is intentional vs system-broken.
            pf = load_portfolio()
            wcount = len(pf.get("watch_levels", []))
            ocount = len(pf.get("open_trades", []))
            pcount = len(pf.get("pending_recommendations", []))
            # Distinguish "Sonnet legitimately silent" from "Sonnet failed".
            # Bug 2026-05-07: stop_sequences killed Sonnet at out=3 tokens, no
            # tool_use, no text — _check sent "Morning OK" while bot was blind.
            tr = pf.get("last_morning_trace") or {}
            tool_called = bool(tr.get("tool_called"))
            sonnet_failed = (
                not tool_called
                and (tr.get("output_tokens") or 0) < 20
                and not tr.get("sonnet_text")
            )
            if sonnet_failed:
                send_alert(
                    "🚨 MORNING FAIL — Sonnet schwieg",
                    f"Sonnet emittierte {tr.get('output_tokens')} tokens, "
                    f"stop={tr.get('stop_reason')}, kein tool_use, kein text. "
                    f"Bot ist heute BLIND (keine Watchlevels, keine Setup-Detection). "
                    f"Manuell prüfen + ggf. /morning erneut.",
                )
                logger.error(
                    "Morning brief: Sonnet returned empty (out_tok=%s, stop=%s) — "
                    "alert sent, NOT marking morning prep done",
                    tr.get("output_tokens"), tr.get("stop_reason"),
                )
                return  # Don't mark done so next loop tick retries.
            if wcount > 0:
                reason = f"{wcount} Watchlevel(s) für heute aktiv"
            elif ocount > 0:
                reason = "Keine neuen A+ Setups, laufende Positionen halten"
            else:
                reason = "Keine A+ Setups, kein Watchlevel, kein offener Trade"
            send_notification(
                f"🌅 *Morning OK* | {datetime.now().strftime('%H:%M')}\n"
                f"Watchlevels: {wcount} | Positionen: {ocount} | Pending: {pcount}\n"
                f"Status: {reason}"
            )
            logger.info("Morning brief: tool-only call, status heartbeat sent")
        else:
            send_daily_summary(analysis)
            logger.info("✅ Morning prep sent")
        _transient_retry_reset("morning")
        _mark_morning_prep_done()
    except Exception as e:
        if _is_transient(e):
            n = _transient_retry_inc("morning")
            logger.warning(
                "Morning prep transient error (%d/%d): %s",
                n, _TRANSIENT_RETRY_CAP, e,
            )
            if n >= _TRANSIENT_RETRY_CAP:
                send_alert(
                    "Morning Prep Error",
                    f"{e}\n\n{n} transiente Versuche fehlgeschlagen — aufgegeben. Manuell: /morning",
                )
                _mark_morning_prep_done()
            # else: silent skip — next 15-min loop tick retries naturally
        else:
            logger.exception("Morning prep failed (persistent)")
            send_alert("Morning Prep Error", f"{e}\n\nKein Auto-Retry. Manuell: /morning")
            _mark_morning_prep_done()


def run_opening_check(market: str):
    """Lightweight gap-check shortly after market open.
    Only sends a notification when Claude flags real action."""
    if _opening_check_done_today(market):
        return

    portfolio = load_portfolio()
    if kill_switch_active(portfolio):
        logger.info("%s open check skipped: kill-switch active", market.upper())
        _mark_opening_check_done(market)
        return
    if not portfolio.get("open_trades") and not portfolio.get("watch_levels"):
        logger.info("%s open check skipped — no open trades or watch levels", market.upper())
        _mark_opening_check_done(market)
        return

    logger.info("🔔 Running %s open check...", market.upper())

    try:
        label = "XETRA" if market == "xetra" else "US"
        analysis = analyze_portfolio(mode="opening", event_context=f"{label} Open")

        if analysis.startswith("⚠️ Analysis skipped"):
            logger.info("%s open check: %s", market.upper(), analysis)
            return

        if "(keine Text-Analyse)" in analysis:
            logger.info("%s open: tool-only call, Telegram already sent by tool handler",
                        market.upper())
        else:
            pf = load_portfolio()
            parsed = _parse_actionable(analysis, portfolio=pf)
            if parsed:
                extras = _enrich_extras_for_add(parsed, {"Open": label}, portfolio=pf)
                send_actionable(
                    parsed["action"], parsed["ticker"],
                    size=None, reason=parsed["reason"], extras=extras,
                )
                logger.info("✅ %s open check sent (action flagged)", market.upper())
            else:
                logger.info("%s open: non-actionable verdict, no notification: %s",
                            market.upper(), analysis[:80])

        _transient_retry_reset(f"opening_{market}")
        _mark_opening_check_done(market)
    except Exception as e:
        if _is_transient(e):
            key = f"opening_{market}"
            n = _transient_retry_inc(key)
            logger.warning(
                "%s open check transient error (%d/%d): %s",
                market.upper(), n, _TRANSIENT_RETRY_CAP, e,
            )
            if n >= _TRANSIENT_RETRY_CAP:
                send_alert(
                    f"{market.upper()} Open Check Error",
                    f"{e}\n\n{n} transiente Versuche fehlgeschlagen — aufgegeben.",
                )
                _mark_opening_check_done(market)
            # else: silent skip — next 15-min loop tick retries naturally
        else:
            logger.exception("%s open check failed (persistent)", market.upper())
            send_alert(f"{market.upper()} Open Check Error", f"{e}\n\nKein Auto-Retry heute.")
            _mark_opening_check_done(market)


def run_event_check():
    """Check for events and trigger analysis if needed."""
    if not is_market_hours():
        return
    if kill_switch_active(load_portfolio()):
        logger.debug("Event check skipped: kill-switch active")
        return

    try:
        events = detect_events()

        if not events:
            return

        # Refactor 2026-05-21: WATCH_LEVEL_NOTIFY events sind Entry-Search-Watch-Hits auf
        # Tickern OHNE offene Position. Sonnet's Morning-Limit-Buy-Rec ist bereits das
        # Action-Signal — Haiku macht KEINE neuen Entry-Recs aus diesen Hits. Telegram-Notify
        # only, kein Claude-Call. WATCH_LEVEL_HIT (Defense-Hits auf offenen Positionen) +
        # WATCH_INVALIDATED gehen weiterhin durch analyze_portfolio.
        notify_events = [e for e in events if e.get("type") == "WATCH_LEVEL_NOTIFY"]
        analyze_events = [e for e in events if e.get("type") != "WATCH_LEVEL_NOTIFY"]

        if notify_events:
            for ev in notify_events:
                ticker = ev.get("ticker", "?")
                price = ev.get("current_price", 0)
                level_type = ev.get("level_type", "?")
                trigger = ev.get("trigger_price", 0)
                thesis = ev.get("thesis", "")
                source = ev.get("source", "")
                msg = (
                    f"📍 *Watch-Hit: {ticker}* @ €{price:.2f}\n"
                    f"Setup: {level_type} (Trigger €{trigger:.2f})\n"
                )
                if thesis:
                    msg += f"These: {_word_truncate(thesis, 150)}\n"
                # Source-spezifischer Footer: Sonnet's Plan vs Auto-Watch.
                if source == "geo_news_auto":
                    msg += "_⚠️ Auto-Watch aus Geo-News — KEIN Sonnet-Plan. Prüfe selbst ob Entry sinnvoll (oft Late/Chase-Pattern)._"
                else:
                    msg += "_Sonnet's Morning-Limit-Buy aktiv? Check /pending_"
                send_notification(msg)
            logger.info(
                "🔔 %d watch-notify(s) sent (Telegram-only): %s",
                len(notify_events),
                ", ".join(e.get("ticker", "?") for e in notify_events),
            )

        if not analyze_events:
            return

        should_analyze, reason = should_analyze_events(analyze_events)

        logger.info("🔔 %d defense/invalidate event(s) - %s", len(analyze_events), reason)

        if not should_analyze:
            return

        event_descriptions = []
        for event in analyze_events:
            desc = f"{event['ticker']} @ ${event['trigger_price']:.2f} ({event['level_type']})"
            if event.get("note"):
                desc += f" - {event['note']}"
            event_descriptions.append(desc)

        event_context = " | ".join(event_descriptions)

        analysis = analyze_portfolio(mode="event", event_context=event_context)

        # Tool-call path (recommend_entry/recommend_add) already sent its own rich
        # Telegram from analyzer.py. Cooldown / API-skip just logs. Otherwise parse
        # Claude's text verdict into the schema and forward as ONE actionable msg.
        if analysis.startswith("⚠️ Analysis skipped"):
            logger.info("Event analysis skipped: %s", analysis)
        elif "(keine Text-Analyse)" in analysis:
            logger.info("Event: tool-only call, Telegram already sent by tool handler")
        else:
            pf = load_portfolio()
            parsed = _parse_actionable(analysis, portfolio=pf)
            if parsed:
                extras = _enrich_extras_for_add(
                    parsed, {"Watch": _word_truncate(event_context, 150)},
                    portfolio=pf,
                )
                send_actionable(
                    parsed["action"], parsed["ticker"],
                    size=None, reason=parsed["reason"], extras=extras,
                )
                logger.info("✅ Event verdict sent: %s", analysis.split('\n')[0][:80])
            else:
                logger.info("Event non-actionable, suppressed: %s", analysis.split('\n')[0][:80])

    except Exception:
        logger.exception("Event check failed")


_SLTP_DEDUP_BUCKETS = {
    # 1× per day per trade — pure noise warning shouldn't re-fire on every 15min tick
    "STOP_LOSS_WARNING": 86400,
    "TRAILING_STOP_MOVED": 900,    # 1× per 15min per trade — chatty during runs
    "TRAILING_ACTIVATED": 86400,
    "BREAK_EVEN_SHIFT": 86400,
    # Default 0 = once-per-trade (HITs, PARTIAL_TP)
}


def _maybe_send_sltp(alert: dict, message: str) -> None:
    """SL/TP alert with per-trade dedup. Tick-storm protection: same alert during a
    15min stale-data window doesn't double-fire. Caller passes pre-rendered message."""
    bucket = _SLTP_DEDUP_BUCKETS.get(alert.get("type"), 0)
    key = (
        f"{alert.get('type')}:{int(time.time() // bucket)}" if bucket
        else f"{alert.get('type')}:once"
    )
    with portfolio_lock:
        pf = load_portfolio()
        trade = next(
            (t for t in pf.get("open_trades", [])
             if (t.get("ticker") or "").upper() == (alert.get("ticker") or "").upper()),
            None,
        )
        if trade is None:
            send_notification(message)
            return
        keys = trade.setdefault("alerted_keys", [])
        if key in keys:
            logger.debug("SL/TP alert deduped: %s %s", alert.get("ticker"), key)
            return
        keys.append(key)
        trade["alerted_keys"] = keys[-50:]
        save_portfolio(pf)
    send_notification(message)


def run_price_check():
    """Check for price alerts and stop-loss/take-profit triggers."""
    if not is_market_hours():
        return

    logger.debug("Checking prices...")

    try:
        # Check stop-loss and take-profit for open trades
        sl_tp_alerts = check_stop_loss_take_profit(paper=False)
        # Paper-Portfolio SL/TP läuft parallel: gleiche Logik, eigenes File, KEIN
        # Telegram-Alert (User soll nicht für Paper-Closes gespammt werden). Fail-soft.
        try:
            check_stop_loss_take_profit(paper=True)
        except Exception:
            logger.exception("paper SL/TP loop failed")

        for alert in sl_tp_alerts:
            if alert["type"] == "STOP_LOSS_HIT":
                message = f"""🚨 *STOP-LOSS HIT: {alert['ticker']}*

Entry: ${alert['entry']:.2f}
Stop: ${alert['stop_loss']:.2f}
Now: ${alert['current_price']:.2f}
P&L: {alert['pnl_pct']:.1f}%

⚠️ *CLOSE POSITION NOW*"""
                _maybe_send_sltp(alert, message)

                # Log to MemPalace
                if MEMPALACE_AVAILABLE:
                    log_trade(alert, "STOP_HIT", f"Stop-Loss getriggert bei ${alert['current_price']:.2f}")
                
            elif alert["type"] == "TAKE_PROFIT_HIT":
                partial_note = "TP1 (Teil-Verkauf)" if alert.get("partial") else "Full TP"
                message = f"""🎉 *TAKE-PROFIT HIT: {alert['ticker']}* ({partial_note})

Entry: ${alert['entry']:.2f}
Target: ${alert['take_profit']:.2f}
Now: ${alert['current_price']:.2f}
P&L: +{alert['pnl_pct']:.1f}%

💰 *TAKE PROFITS*"""
                _maybe_send_sltp(alert, message)

                if MEMPALACE_AVAILABLE:
                    action = "TP1_HIT" if alert.get("partial") else "TP_HIT"
                    log_trade(alert, action, f"Take-Profit erreicht bei ${alert['current_price']:.2f}")

            elif alert["type"] == "PARTIAL_TP_HIT":
                message = (
                    f"🎯 *PARTIAL-TP: {alert['ticker']}*\n\n"
                    f"Entry: €{alert['entry']:.2f}\n"
                    f"TP1: €{alert['take_profit']:.2f}\n"
                    f"Now: €{alert['current_price']:.2f}\n"
                    f"P&L: +{alert['pnl_pct']:.1f}%\n\n"
                    f"💰 *VERKAUFE {alert['shares_sold']} Stk auf TR jetzt*\n"
                    f"_Rest {alert['shares_remaining']} Stk läuft mit BE-SL + Trailing weiter._"
                )
                _maybe_send_sltp(alert, message)
                if MEMPALACE_AVAILABLE:
                    log_trade(alert, "PARTIAL_TP", f"Partial-TP @ €{alert['current_price']:.2f}, "
                              f"sold {alert['shares_sold']}, remain {alert['shares_remaining']}")

            elif alert["type"] == "BREAK_EVEN_SHIFT":
                _maybe_send_sltp(alert, (
                    f"🛡️ *Stop auf Break-Even: {alert['ticker']}*\n\n"
                    f"Neuer Stop: ${alert['new_stop']:.2f}\n"
                    f"_Rest-Position läuft risikofrei weiter._"
                ))

            elif alert["type"] == "TRAILING_ACTIVATED":
                _maybe_send_sltp(alert, (
                    f"📐 *Trailing aktiviert: {alert['ticker']}*\n\n"
                    f"Trail: {alert['trail_pct']}% (1.5× ATR {alert['atr_pct']}%)\n"
                    f"_Runner-Schutz: SL zieht ab jetzt automatisch nach._"
                ))

            elif alert["type"] == "TRAILING_STOP_MOVED":
                _maybe_send_sltp(alert, (
                    f"📈 *Trailing-Stop nachgezogen: {alert['ticker']}*\n\n"
                    f"Neuer Stop: ${alert['new_stop']:.2f}\n"
                    f"Preis: ${alert['current_price']:.2f}"
                ))

            elif alert["type"] == "STOP_LOSS_WARNING":
                message = f"""⚠️ *Approaching Stop: {alert['ticker']}*

Stop: ${alert['stop_loss']:.2f}
Now: ${alert['current_price']:.2f}
Distance: {alert['distance_pct']:.1f}%

_Watch closely_"""
                _maybe_send_sltp(alert, message)
        
        # Price alerts → Claude analysis → only notify if actionable (BUY/SELL)
        # Skipped under kill-switch (no new entries; SL/TP loop above keeps running).
        price_alerts = [] if kill_switch_active(load_portfolio()) else check_price_alerts()

        if price_alerts:
            descriptions = []
            for a in price_alerts:
                emoji = "📉" if a["type"] == "DROP" else "📈"
                descriptions.append(
                    f"{emoji} {a['ticker']} {a['change']:+.1f}% @ {a['price']:.2f}"
                )
            event_context = " | ".join(descriptions)
            logger.info("🔔 %d price alert(s): %s", len(price_alerts), event_context)

            big_movers = [
                a for a in price_alerts
                if abs(a.get("change", 0)) >= config.BIG_MOVER_PCT_BYPASS
            ]
            bypass = bool(big_movers)
            if bypass:
                logger.info(
                    "Big-mover cooldown bypass: %s",
                    ", ".join(f"{a['ticker']} {a['change']:+.1f}%" for a in big_movers),
                )
            analysis = analyze_portfolio(
                mode="event", event_context=event_context, bypass_cooldown=bypass,
            )

            if analysis.startswith("⚠️ Analysis skipped"):
                logger.info("Price alert analysis skipped: %s", analysis)
            elif "(keine Text-Analyse)" in analysis:
                logger.info("Price alert: tool-only call, Telegram already sent by tool handler")
            else:
                pf = load_portfolio()
                parsed = _parse_actionable(analysis, portfolio=pf)
                if parsed:
                    extras = _enrich_extras_for_add(
                        parsed, {"Move": _word_truncate(event_context, 150)},
                        portfolio=pf,
                    )
                    send_actionable(
                        parsed["action"], parsed["ticker"],
                        size=None, reason=parsed["reason"], extras=extras,
                    )
                    logger.info("✅ Price alert verdict sent: %s", analysis.split('\n')[0][:80])
                else:
                    logger.info("Price alert non-actionable, suppressed: %s", analysis.split('\n')[0][:80])
    except Exception:
        logger.exception("Price check failed")


def _auto_watch_geo(commodities: list[str], headline: str):
    """Set breakout-long watch-levels at +5% on matched commodities (GEO trigger).
    Skips tickers that already have an active watch-level."""
    if not commodities:
        return
    market = get_market_data(commodities)
    with portfolio_lock:
        portfolio = load_portfolio()
        existing = {w.get("ticker") for w in portfolio.get("watch_levels", [])}
        added = []
        for ticker in commodities:
            if ticker in existing:
                continue
            data = market.get(ticker, {})
            price = data.get("price") if isinstance(data, dict) else None
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            trigger = round(price * 1.05, 2)
            portfolio.setdefault("watch_levels", []).append({
                "ticker": ticker,
                "type": "breakout_long",
                "trigger_price": trigger,
                "note": f"GEO-Auto: {headline[:60]}",
                "source": "geo_news_auto",
                "created_date": str(date.today()),
            })
            added.append(f"{ticker}@€{trigger:.2f}")
        if added:
            save_portfolio(portfolio)
            logger.info("GEO auto-watch added: %s", ", ".join(added))


_news_check_consec_failures = 0
_news_check_alert_sent = False


def run_news_check():
    """Scan for new actionable headlines. Geo news forces analysis; stock news respects cooldown.
    Runs during market hours + Sunday 18-22 CET (weekend geo-news catch-up)."""
    global _news_check_consec_failures, _news_check_alert_sent
    if not (is_market_hours() or is_weekend_news_window()):
        return
    if kill_switch_active(load_portfolio()):
        logger.debug("News check skipped: kill-switch active")
        return

    try:
        news_events = check_news_events()
        if not news_events:
            return

        geo = [e for e in news_events if e["type"] == "NEWS_GEO"]
        stocks = [e for e in news_events if e["type"] == "NEWS_STOCK"]

        # Geopolitical: bypass cooldown but notify only on actionable verdict.
        # Dedup per commodity in a 6h window — Iran-Krieg-day produces 5-10 related
        # headlines that all map to the same `triggered_commodities`. Without dedup
        # each one runs a forced analyze_portfolio (yfinance + Claude), burning
        # daily-cap and producing redundant verdicts (Bug 2026-05-02 audit).
        _geo_pf = load_portfolio()
        _geo_seen = _geo_pf.get("geo_news_fired", {}) or {}
        _now_iso = datetime.now().isoformat()
        _cutoff = (datetime.now() - timedelta(hours=6)).isoformat()
        _dirty = False

        for event in geo:
            comms = ", ".join(event["triggered_commodities"])
            headline = event["headline"]
            comm_key = "+".join(sorted(event["triggered_commodities"]))
            last_fired = _geo_seen.get(comm_key)
            if last_fired and last_fired > _cutoff:
                logger.info("GEO dedup: %s (%s) suppressed — fired %s",
                            comm_key, headline[:60], last_fired)
                continue
            logger.info("📰 GEO NEWS → %s: %s", comms, headline)

            # Auto-watch deaktiviert 2026-05-21. _auto_watch_geo erstellte breakout_long
            # Watches bei +5% über CURRENT price = exakt Late-Entry-Pattern (vertikale
            # News-Spikes sind HARD-BLOCK per Strategy "NICHT chase auf erste impulsive
            # Candle"). Geo-News-Trigger läuft trotzdem durch analyze_portfolio (unten) —
            # wenn Haiku die News als echten Catalyst sieht, kann es recommend_entry mit
            # passendem Entry-Preis machen. Der Auto-Watch hat das bypassed mit naivem
            # +5%-Trigger der parabolic-late-Stocks pumped. Beispiel heute 3OIL.MI +6% /
            # 3BRL.MI +5.8% bei atr% 12 — wäre alles als Watch-Hit getriggert.
            # try:
            #     _auto_watch_geo(event["triggered_commodities"], headline)
            # except Exception:
            #     logger.exception("GEO auto-watch failed")

            ctx = f"GEO NEWS: {headline} | Commodity-Play: {comms}"
            analysis = analyze_portfolio(mode="event", event_context=ctx, force=True)
            _geo_seen[comm_key] = _now_iso
            _dirty = True
            pf = load_portfolio()
            parsed = _parse_actionable(analysis, portfolio=pf)
            if parsed:
                extras = _enrich_extras_for_add(
                    parsed,
                    {"GEO": _word_truncate(headline, 150), "Setup": comms},
                    portfolio=pf,
                )
                send_actionable(
                    parsed["action"], parsed["ticker"],
                    size=None, reason=parsed["reason"], extras=extras,
                )
            else:
                logger.info("GEO news non-actionable, suppressed: %s",
                            (analysis or "").split('\n')[0][:80])

        if _dirty:
            with portfolio_lock:
                _pf = load_portfolio()
                _existing = _pf.get("geo_news_fired", {}) or {}
                _existing.update(_geo_seen)
                # Prune entries older than 24h.
                _prune_cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
                _existing = {k: v for k, v in _existing.items() if v > _prune_cutoff}
                _pf["geo_news_fired"] = _existing
                save_portfolio(_pf)

        # Stock news pre-gate: skip Claude call when ticker has neither open position
        # nor active watch_level — no thesis to verify, no position to manage, no
        # actionable verdict possible. Disable via NEWS_REQUIRE_OPEN_OR_WATCH=False.
        if stocks and config.NEWS_REQUIRE_OPEN_OR_WATCH:
            _pf = load_portfolio()
            _relevant = (
                {(t.get("ticker") or "").upper() for t in _pf.get("open_trades", [])}
                | {(w.get("ticker") or "").upper() for w in _pf.get("watch_levels", [])}
            )
            _filtered = [
                s for s in stocks
                if (s.get("source_ticker") or "").upper() in _relevant
            ]
            if not _filtered:
                logger.info("Stock news on irrelevant tickers, skipping Claude: %s",
                            [s.get("source_ticker") for s in stocks])
                stocks = []
            else:
                stocks = _filtered

        if stocks:
            headlines = " | ".join(e["headline"] for e in stocks[:3])
            tickers = list({e["source_ticker"] for e in stocks})
            logger.info("📰 STOCK NEWS (%d): %s", len(stocks), headlines[:120])
            ctx = f"NEWS: {headlines}"
            analysis = analyze_portfolio(mode="event", event_context=ctx)
            pf = load_portfolio()
            parsed = _parse_actionable(analysis, portfolio=pf)
            if parsed:
                extras = _enrich_extras_for_add(
                    parsed, {"News": _word_truncate(headlines, 150)},
                    portfolio=pf,
                )
                send_actionable(
                    parsed["action"], parsed["ticker"],
                    size=None, reason=parsed["reason"], extras=extras,
                )
            else:
                logger.info("Stock news non-actionable, suppressed: %s",
                            (analysis or "").split('\n')[0][:80])

        _news_check_consec_failures = 0
        _news_check_alert_sent = False
    except Exception:
        logger.exception("News check failed")
        _news_check_consec_failures += 1
        if _news_check_consec_failures >= 3 and not _news_check_alert_sent:
            try:
                send_alert(
                    "⚠️ News-Pipeline tot",
                    f"{_news_check_consec_failures}× consecutive failures. "
                    f"Bot ist News-blind bis Fix. Logs prüfen.",
                )
                _news_check_alert_sent = True
            except Exception:
                logger.exception("News-pipeline alert send failed")


def graceful_shutdown(signum, frame):
    """Handle shutdown gracefully."""
    logger.info("👋 Shutting down Trading Advisor...")
    sys.exit(0)


def _startup_cleanup():
    """Prune stale per-day fields that older code paths may have left behind.

    Per-day prunes already happen on each save (seen_news, triggered_events,
    triggered_price_alerts), but a long-uptime bot or a downgrade from an older
    version can leave residue. One-shot scrub at startup keeps portfolio.json
    lean without relying on every save path being perfect.
    """
    try:
        with portfolio_lock:
            pf = load_portfolio()
            today = str(date.today())
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            dirty = False

            # seen_news: keep today + yesterday only
            sn = pf.get("seen_news") or {}
            if sn:
                sn_pruned = {k: v for k, v in sn.items() if k in (today, yesterday)}
                if len(sn_pruned) != len(sn):
                    pf["seen_news"] = sn_pruned
                    dirty = True
                    logger.info("Cleanup: seen_news %d → %d days", len(sn), len(sn_pruned))

            # triggered_events: keep today only (pruned on save but be defensive)
            te = pf.get("triggered_events") or []
            te_today = [t for t in te if t.get("date") == today]
            if len(te_today) != len(te):
                pf["triggered_events"] = te_today
                dirty = True
                logger.info("Cleanup: triggered_events %d → %d", len(te), len(te_today))

            # triggered_price_alerts: keep today only
            tpa = pf.get("triggered_price_alerts") or []
            tpa_today = [t for t in tpa if t.get("date") == today]
            if len(tpa_today) != len(tpa):
                pf["triggered_price_alerts"] = tpa_today
                dirty = True
                logger.info("Cleanup: triggered_price_alerts %d → %d", len(tpa), len(tpa_today))

            # geo_news_fired: prune entries older than 24h
            gnf = pf.get("geo_news_fired") or {}
            if gnf:
                cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
                gnf_pruned = {k: v for k, v in gnf.items() if v > cutoff}
                if len(gnf_pruned) != len(gnf):
                    pf["geo_news_fired"] = gnf_pruned
                    dirty = True
                    logger.info("Cleanup: geo_news_fired %d → %d entries",
                                len(gnf), len(gnf_pruned))

            # equity_history: prune to 14d rolling
            eh = pf.get("equity_history") or []
            if eh:
                cutoff_eh = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M")
                eh_pruned = [p for p in eh if (p.get("ts") or "") >= cutoff_eh]
                if len(eh_pruned) != len(eh):
                    pf["equity_history"] = eh_pruned
                    dirty = True
                    logger.info("Cleanup: equity_history %d → %d points",
                                len(eh), len(eh_pruned))

            if dirty:
                save_portfolio(pf)
                logger.info("✅ Startup cleanup persisted")
    except Exception:
        logger.exception("Startup cleanup failed (non-fatal)")


def main():
    """Main entry point."""
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

    # One-shot scrub of per-day fields that may have accumulated across restarts.
    _startup_cleanup()

    # Background Telegram listener for /confirm, /close, /positions, /cancel
    start_listener_thread()

    # Background hang-detection watchdog. Daemon=True → dies with main process.
    threading.Thread(target=_heartbeat_watchdog, daemon=True, name="heartbeat-watchdog").start()

    if is_morning_prep_time() or (is_market_hours() and not _morning_prep_done_today()):
        run_morning_prep()

    logger.info("⏰ Monitoring aktiv. Ctrl+C zum Stoppen.")
    
    last_check = datetime.now()
    check_interval = config.PRICE_CHECK_INTERVAL_MINUTES * 60
    # Heartbeat write throttle: web stale-warn fires at ageSec>120, so 60s is safe.
    # Cuts ~5000 portfolio.json writes/day to ~840 (atomic-write cost scales with
    # file size; taming this also protects price-check loop latency).
    last_heartbeat_write = 0.0
    HEARTBEAT_WRITE_INTERVAL_SEC = 60

    while True:
        _heartbeat[0] = time.monotonic()
        now = datetime.now()

        # Persist heartbeat to portfolio.json so the web dashboard can show
        # last-tick age + API spend + live unrealized P&L without re-implementing
        # fs scans. Throttled to ≤1×/min.
        if (time.monotonic() - last_heartbeat_write) >= HEARTBEAT_WRITE_INTERVAL_SEC:
            try:
                from core import get_daily_usage
                from core.livefeed import live_quote_for_ticker
                _live_prices: dict[str, float] = {}
                _live_quotes: dict[str, dict] = {}
                try:
                    _pf_snapshot = load_portfolio()
                    _open = _pf_snapshot.get("open_trades", []) or []
                    _watch = _pf_snapshot.get("watch_levels", []) or []
                    _tickers = list({
                        *(t["ticker"] for t in _open if t.get("ticker")),
                        *(w["ticker"] for w in _watch if w.get("ticker")),
                    })
                    # Heartbeat fast-path: skip yfinance entirely, scrape ls-tc direct.
                    # 60s yfinance cache stays untouched (still serves indicator pipeline)
                    # while live_quotes refresh at LS-TC's 10s cache cadence.
                    for _tk in _tickers:
                        _q = live_quote_for_ticker(_tk)
                        if not _q or not _q.get("price"):
                            continue
                        _live_prices[_tk] = float(_q["price"])
                        _live_quotes[_tk] = {
                            "price": _q.get("price"),
                            "bid": _q.get("bid"),
                            "ask": _q.get("ask"),
                            "ts": _q.get("ts"),
                            "change_pct": _q.get("change_pct"),
                            "market_status": _q.get("market_status"),
                            "source": _q.get("source"),
                        }
                except Exception:
                    logger.exception("Live-price snapshot failed (heartbeat)")
                with portfolio_lock:
                    _pf = load_portfolio()
                    _pf["heartbeat"] = {
                        "last_tick": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "market_hours": is_market_hours(),
                        "api_calls_today": get_daily_usage(),
                        "api_cap": config.MAX_ANALYSES_PER_DAY,
                        "prices": _live_prices,
                        "live_quotes": _live_quotes,
                    }
                    # MAE/MFE accumulation: track per open trade the worst (mae) and
                    # best (mfe) price seen since entry. Frozen onto closed_trade in
                    # /close handler. Insight: where would 1-bar-tighter SL have
                    # caught more profit, where would looser SL have prevented stops.
                    # build_trade_dict seeds both at entry; we only ratchet here.
                    _unrealized_eur = 0.0
                    for _ot in _pf.get("open_trades", []) or []:
                        _tk = (_ot.get("ticker") or "").upper()
                        _live = _live_prices.get(_tk)
                        if not isinstance(_live, (int, float)) or _live <= 0:
                            continue
                        _entry = float(_ot.get("entry_price") or 0)
                        _shares = float(_ot.get("shares") or 0)
                        if _entry <= 0:
                            continue
                        _mae = _ot.get("mae", _entry)
                        _mfe = _ot.get("mfe", _entry)
                        if _live < _mae:
                            _ot["mae"] = round(_live, 4)
                        if _live > _mfe:
                            _ot["mfe"] = round(_live, 4)
                        # max_r_open: best R-Multiple während Position offen war (für
                        # outcome-Analyse: hatten wir vor SL einen Gewinn-Peak?).
                        _irs = _ot.get("initial_risk_per_share")
                        if isinstance(_irs, (int, float)) and _irs > 0:
                            _r_now = (_live - _entry) / _irs
                            _max_r = _ot.get("max_r_open", 0.0) or 0.0
                            if _r_now > _max_r:
                                _ot["max_r_open"] = round(_r_now, 3)
                        if _shares > 0:
                            _unrealized_eur += (_live - _entry) * _shares

                    # Equity history: append a snapshot every heartbeat so the
                    # dashboard can render an intraday equity curve, not just a
                    # static line connecting realized events. Pruned to last 14
                    # days (rolling window). Skip if no live quotes — keeps the
                    # series clean across off-hours / LS-TC outages instead of
                    # padding with stale points.
                    if _live_prices:
                        _starting = float(
                            _pf.get("total_capital_eur", config.BUDGET_EUR)
                            or config.BUDGET_EUR
                        )
                        _realized = sum(
                            float(t.get("pnl_eur") or 0)
                            for t in _pf.get("closed_trades", []) or []
                        )
                        _movements = sum(
                            float(m.get("amount") or 0)
                            for m in _pf.get("cash_movements", []) or []
                        )
                        _equity_now = round(
                            _starting + _realized + _movements + _unrealized_eur, 2,
                        )
                        _hist = _pf.get("equity_history", []) or []
                        # Dedup: skip append if same minute already recorded
                        # (heartbeat throttle is 60s but if it overshoots we
                        # avoid duplicates).
                        _ts = now.strftime("%Y-%m-%d %H:%M")
                        if not _hist or _hist[-1].get("ts") != _ts:
                            _hist.append({
                                "ts": _ts,
                                "equity": _equity_now,
                                "unrealized": round(_unrealized_eur, 2),
                                "market_hours": is_market_hours(),
                            })
                        # Prune to last 14 days. ts string sorts lexicographically.
                        _cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M")
                        _hist = [p for p in _hist if (p.get("ts") or "") >= _cutoff]
                        _pf["equity_history"] = _hist

                    save_portfolio(_pf)
                last_heartbeat_write = time.monotonic()
            except Exception:
                logger.exception("Heartbeat persist failed")

        if is_morning_prep_time():
            run_morning_prep()

        # Time-window checks: each fires once per day (dedup via portfolio.json)
        if is_xetra_open_check_time():
            run_opening_check("xetra")
        if is_us_open_check_time():
            run_opening_check("us")
        if is_eod_summary_time():
            run_eod_summary()
        if is_weekend_summary_time():
            run_weekend_summary()

        if (now - last_check).total_seconds() >= check_interval:
            if is_market_hours():
                # Auto-kill check fires before other work — if VIX/SPX shocked,
                # skip new entries this cycle (event_check + news_check honor kill).
                try:
                    shock = maybe_auto_kill()
                    if shock:
                        send_alert(
                            "🛑 AUTO-KILL aktiviert",
                            f"{shock['reason']}\n\nKill-Switch flipped automatisch. "
                            f"SL/TP-Monitoring läuft weiter. "
                            f"Manuell aufheben via `/resume`."
                        )
                except Exception:
                    logger.exception("auto-kill check failed")
                run_price_check()
                run_event_check()
                run_news_check()
                try:
                    _check_exit_reminders()
                except Exception:
                    logger.exception("Exit-reminder check failed")
                # Drawdown-cross + new-ATH alerts. State persisted; each
                # threshold fires once per peak-cycle.
                _check_equity_alerts()
            elif is_weekend_news_window():
                # Sunday evening: news-only scan, no price/event checks (markets closed)
                run_news_check()

            last_check = now

        time.sleep(10)


if __name__ == "__main__":
    main()
