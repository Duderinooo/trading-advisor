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
from datetime import datetime, date
from logging.handlers import RotatingFileHandler

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


def _enrich_extras_for_add(parsed: dict, base_extras: dict) -> dict:
    """When Claude emits 'ADD: ...' as TEXT (e.g. tool-call gates blocked or Sonnet-lazy),
    look up the open trade and surface Bestand/SL so user has actionable context.
    Without this, ADD text-mode shows just '🎯 ADD | TICKER' with no size hint."""
    if (parsed or {}).get("action") != "ADD":
        return base_extras
    portfolio = load_portfolio()
    open_trade = next(
        (t for t in portfolio.get("open_trades", [])
         if (t.get("ticker") or "").upper() == parsed["ticker"]),
        None,
    )
    if not open_trade:
        return base_extras
    enriched = dict(base_extras)
    enriched["Bestand"] = (
        f"€{float(open_trade.get('size_eur') or 0):.0f} "
        f"@ €{float(open_trade.get('entry_price') or 0):.2f}"
    )
    sl = open_trade.get("stop_loss")
    if sl:
        enriched["SL"] = f"€{float(sl):.2f}"
    enriched["Hinweis"] = "Size+Preis via /add command bestimmen"
    return enriched


def _parse_actionable(analysis: str) -> dict | None:
    """Parse Claude's verdict line into a structured dict, or None if non-actionable.

    Returns {"action": ENTRY|EXIT|ADD|REDUCE, "ticker": ..., "reason": ...}
    Drops PASS/HALTEN/HOLD verdicts and anything that doesn't match the grammar
    (markdown preamble, multi-line analysis, etc.).
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
    return {
        "action": _ACTION_NORMALIZE.get(raw, raw),
        "ticker": m.group("ticker").upper(),
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
        from datetime import timedelta
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
        stats = compute_hit_stats(portfolio.get("closed_trades", []))

        # Mistake distribution (last 20 losses) — surfaces drift
        last_losses = [
            t for t in portfolio.get("closed_trades", [])
            if (t.get("pnl_pct") or 0) <= 0
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


def _check_exit_reminders():
    """Resend Telegram for pending EXIT-recs that the user hasn't actioned in time.
    Bot doesn't auto-close (no broker API) — if user ignores `EXIT now`, position
    rots. This nudges. Each rec gets max 1 reminder (idempotent via flag)."""
    if not is_market_hours():
        return
    today = str(date.today())
    with portfolio_lock:
        portfolio = load_portfolio()
        pending = portfolio.get("pending_recommendations", []) or []
        if not pending:
            return
        now = datetime.now()
        dirty = False
        for rec in pending:
            if rec.get("kind") != "exit":
                continue
            if rec.get("reminder_sent_date") == today:
                continue
            ts = rec.get("timestamp")
            if not ts:
                continue
            try:
                rec_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            urgency = (rec.get("urgency") or "today").lower()
            threshold = _EXIT_REMINDER_THRESHOLDS_MIN.get(urgency, 360)
            age_min = (now - rec_dt).total_seconds() / 60.0
            if urgency == "eod":
                # 1h before XETRA close (17:30) regardless of age
                close_min = 17 * 60 + 30
                cutoff_min = close_min - threshold
                if now.hour * 60 + now.minute < cutoff_min:
                    continue
            elif age_min < threshold:
                continue
            ticker = rec.get("ticker", "?")
            reason = (rec.get("reason") or "")[:120]
            urg_emoji = {"now": "🚨", "today": "⚠️", "eod": "🕐"}.get(urgency, "⚠️")
            send_notification(
                f"⏰ *EXIT-REMINDER* | `{ticker}` | {urg_emoji} {urgency}\n"
                f"Vor {int(age_min)} Min empfohlen, noch nicht actioned.\n"
                f"Grund: {reason}\n"
                f"_Auf TR schließen + `/close {ticker} @PREIS`._"
            )
            rec["reminder_sent_date"] = today
            dirty = True
            logger.warning("EXIT-reminder sent: %s urgency=%s age=%dmin",
                           ticker, urgency, int(age_min))
        if dirty:
            portfolio["pending_recommendations"] = pending
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


def run_morning_prep():
    """Run morning analysis to prepare for the trading day."""
    if _morning_prep_done_today():
        return

    logger.info("☀️ Running morning prep...")

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
        analysis = analyze_portfolio(mode="morning")
        # Always forward Sonnet's morning verdict — daily alive-ping confirms bot ran.
        # "Keine Setups heute." is one line, fine as heartbeat. Skip only on the
        # tool-only-no-text edge case (nothing to forward).
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
        _mark_morning_prep_done()
    except (ConnectionError, TimeoutError, OSError) as e:
        logger.exception("Morning prep failed (network/IO)")
        send_alert("Morning Prep Error", str(e))
    except Exception as e:
        logger.exception("Morning prep failed (unexpected)")
        send_alert("Morning Prep Error", str(e))


def run_opening_check(market: str):
    """Lightweight gap-check shortly after market open.
    Only sends a notification when Claude flags real action."""
    if _opening_check_done_today(market):
        return

    portfolio = load_portfolio()
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
            parsed = _parse_actionable(analysis)
            if parsed:
                extras = _enrich_extras_for_add(parsed, {"Open": label})
                send_actionable(
                    parsed["action"], parsed["ticker"],
                    size=None, reason=parsed["reason"], extras=extras,
                )
                logger.info("✅ %s open check sent (action flagged)", market.upper())
            else:
                logger.info("%s open: non-actionable verdict, no notification: %s",
                            market.upper(), analysis[:80])

        _mark_opening_check_done(market)
    except Exception as e:
        logger.exception("%s open check failed", market.upper())
        send_alert(f"{market.upper()} Open Check Error", str(e))


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

        # detect_events now returns WATCH_LEVEL_HIT only (BIG_MOVE removed).
        should_analyze, reason = should_analyze_events(events)

        logger.info("🔔 %d watch level(s) hit - %s", len(events), reason)

        if not should_analyze:
            return

        event_descriptions = []
        for event in events:
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
            parsed = _parse_actionable(analysis)
            if parsed:
                extras = _enrich_extras_for_add(
                    parsed, {"Watch": _word_truncate(event_context, 150)},
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
    # Default 0 = once-per-trade (HITs, TIME_STOP, PARTIAL_TP)
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
        sl_tp_alerts = check_stop_loss_take_profit()

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

            elif alert["type"] == "TIME_STOP_HIT":
                message = (
                    f"⏱️ *TIME-STOP: {alert['ticker']}*\n\n"
                    f"Hold: {alert['held_days']}d ohne TP1 → auto-close\n"
                    f"Entry: €{alert['entry']:.2f}\n"
                    f"Now: €{alert['current_price']:.2f}\n"
                    f"P&L: {alert['pnl_pct']:+.1f}%\n\n"
                    f"⚠️ *POSITION AUF TR SCHLIESSEN*\n_Tote Trades binden Heat — Kapital frei für neue Setups._"
                )
                _maybe_send_sltp(alert, message)
                if MEMPALACE_AVAILABLE:
                    log_trade(alert, "TIME_STOP", f"Time-stop hit nach {alert['held_days']}d")

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

            analysis = analyze_portfolio(mode="event", event_context=event_context)

            if analysis.startswith("⚠️ Analysis skipped"):
                logger.info("Price alert analysis skipped: %s", analysis)
            elif "(keine Text-Analyse)" in analysis:
                logger.info("Price alert: tool-only call, Telegram already sent by tool handler")
            else:
                parsed = _parse_actionable(analysis)
                if parsed:
                    extras = _enrich_extras_for_add(
                        parsed, {"Move": _word_truncate(event_context, 150)},
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


def run_news_check():
    """Scan for new actionable headlines. Geo news forces analysis; stock news respects cooldown.
    Runs during market hours + Sunday 18-22 CET (weekend geo-news catch-up)."""
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

        # Geopolitical: bypass cooldown but notify only on actionable verdict
        for event in geo:
            comms = ", ".join(event["triggered_commodities"])
            headline = event["headline"]
            logger.info("📰 GEO NEWS → %s: %s", comms, headline)

            # Auto-add breakout watch-levels for matched commodities so the breakout
            # gets caught by detect_events even if Claude says PASS this round.
            # Why: GEO news is the trigger; the move often comes hours later — we need
            # persistent levels, not a one-shot Claude call.
            try:
                _auto_watch_geo(event["triggered_commodities"], headline)
            except Exception:
                logger.exception("GEO auto-watch failed")

            ctx = f"GEO NEWS: {headline} | Commodity-Play: {comms}"
            analysis = analyze_portfolio(mode="event", event_context=ctx, force=True)
            parsed = _parse_actionable(analysis)
            if parsed:
                extras = _enrich_extras_for_add(
                    parsed,
                    {"GEO": _word_truncate(headline, 150), "Setup": comms},
                )
                send_actionable(
                    parsed["action"], parsed["ticker"],
                    size=None, reason=parsed["reason"], extras=extras,
                )
            else:
                logger.info("GEO news non-actionable, suppressed: %s",
                            (analysis or "").split('\n')[0][:80])

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
            parsed = _parse_actionable(analysis)
            if parsed:
                extras = _enrich_extras_for_add(
                    parsed, {"News": _word_truncate(headlines, 150)},
                )
                send_actionable(
                    parsed["action"], parsed["ticker"],
                    size=None, reason=parsed["reason"], extras=extras,
                )
            else:
                logger.info("Stock news non-actionable, suppressed: %s",
                            (analysis or "").split('\n')[0][:80])

    except Exception:
        logger.exception("News check failed")


def graceful_shutdown(signum, frame):
    """Handle shutdown gracefully."""
    logger.info("👋 Shutting down Trading Advisor...")
    sys.exit(0)


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

    # Background Telegram listener for /confirm, /close, /positions, /cancel
    start_listener_thread()

    # Background hang-detection watchdog. Daemon=True → dies with main process.
    threading.Thread(target=_heartbeat_watchdog, daemon=True, name="heartbeat-watchdog").start()

    if is_morning_prep_time() or (is_market_hours() and not _morning_prep_done_today()):
        run_morning_prep()

    logger.info("⏰ Monitoring aktiv. Ctrl+C zum Stoppen.")
    
    last_check = datetime.now()
    check_interval = config.PRICE_CHECK_INTERVAL_MINUTES * 60

    while True:
        _heartbeat[0] = time.monotonic()
        now = datetime.now()

        # Persist heartbeat to portfolio.json so the web dashboard can show
        # last-tick age + API spend + live unrealized P&L without re-implementing
        # fs scans. yfinance has its own 60s cache so per-tick pulls are cheap.
        try:
            from core import get_daily_usage
            _live_prices: dict[str, float] = {}
            try:
                _open = load_portfolio().get("open_trades", []) or []
                _tickers = [t["ticker"] for t in _open if t.get("ticker")]
                if _tickers and is_market_hours():
                    _md = get_market_data(_tickers)
                    for _tk, _data in _md.items():
                        _p = _data.get("price") if isinstance(_data, dict) else None
                        if isinstance(_p, (int, float)) and _p > 0:
                            _live_prices[_tk] = float(_p)
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
                }
                save_portfolio(_pf)
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
            elif is_weekend_news_window():
                # Sunday evening: news-only scan, no price/event checks (markets closed)
                run_news_check()

            last_check = now

        time.sleep(30)


if __name__ == "__main__":
    main()
