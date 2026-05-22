"""Risk-guard services: equity-DD alerts, exit-reminders, stale-thesis alerts.

Pure deterministic checks. No LLM calls.
"""

import logging
from datetime import date, datetime, timedelta

import config
from core import (
    load_portfolio, save_portfolio, portfolio_lock,
    get_market_data,
)
from notifier import send_alert, send_notification

from runtime.scheduler import is_market_hours


logger = logging.getLogger("trading_advisor.risk_guards")


# Drawdown-cross alert thresholds (% from peak).
_DD_ALERT_THRESHOLDS = (5.0, 10.0, 15.0, 20.0)

_EXIT_REMINDER_THRESHOLDS_MIN = {"now": 120, "today": 360, "eod": 60}


# ============================================================================
# Equity / Drawdown alerts
# ============================================================================

def check_equity_alerts() -> None:
    """Drawdown-threshold-cross + new-ATH alerts. State persisted; each threshold
    fires once per peak-cycle, re-arms on return-to-peak."""
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

            # Fall back to yfinance for tickers without LS-TC heartbeat.
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

            if equity > ath + 0.01 and len(pf.get("closed_trades", []) or []) >= 1:
                send_alert(
                    "📈 EQUITY NEW ATH",
                    f"Live €{equity:.2f} (alt-ATH €{ath:.2f}, "
                    f"+{(equity / cap - 1) * 100:+.2f}% vs Start).",
                )
                pf["equity_ath"] = round(equity, 2)
                ath = equity
                dd_alerts = set()
                dirty = True

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


# ============================================================================
# Exit-reminder + auto-drop
# ============================================================================

def _gap_min(last_at_str, now):
    if not last_at_str:
        return None
    try:
        return (now - datetime.strptime(last_at_str, "%Y-%m-%d %H:%M")).total_seconds() / 60.0
    except ValueError:
        return None


def _send_exit_reminder(rec: dict, age_min: float) -> None:
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


def _drop_exit_rec(rec: dict, portfolio: dict, now: datetime) -> None:
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


def check_exit_reminders() -> None:
    """Reminder + auto-drop for pending exit-recs."""
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

            if urgency == "eod":
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
                else:
                    kept.append(rec)

        if dirty:
            portfolio["pending_recommendations"] = kept
            save_portfolio(portfolio)


# ============================================================================
# Stale-thesis alerts
# ============================================================================

def check_stale_theses() -> None:
    """Alert on positions held longer than hold_days_max. Once per day per trade."""
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
