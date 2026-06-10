"""Price-monitor service: SL/TP loop + price-alert-driven event analysis."""

import logging
import time

import config
from core import (
    analyze_portfolio, check_price_alerts, check_stop_loss_take_profit,
    load_portfolio, save_portfolio, portfolio_lock, kill_switch_active,
)
from memory import log_trade, MEMPALACE_AVAILABLE
from notifier import send_notification

from runtime.scheduler import is_market_hours


logger = logging.getLogger("trading_advisor.price_monitor")


_SLTP_DEDUP_BUCKETS = {
    "STOP_LOSS_WARNING": 86400,
    "TRAILING_STOP_MOVED": 900,
    "TRAILING_ACTIVATED": 86400,
    "BREAK_EVEN_SHIFT": 86400,
}


def ratchet_open_trade_extremes(open_trades: list, live_prices: dict) -> float:
    """Ratchet MAE/MFE/max_r_open on open trades. Mutates trades in-place.
    Returns unrealized EUR over the same set so caller can fold into equity-curve."""
    unrealized_eur = 0.0
    for ot in open_trades:
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
    return unrealized_eur


def _maybe_send_sltp(alert: dict, message: str) -> None:
    """SL/TP alert with per-trade dedup."""
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


def run_price_check() -> None:
    """SL/TP loop + price-alert event-analysis dispatch."""
    if not is_market_hours():
        return

    logger.debug("Checking prices...")

    try:
        sl_tp_alerts = check_stop_loss_take_profit(paper=False)
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

        # Price alerts → Claude analysis (skipped under kill-switch).
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

        # Unconditional per-cycle heartbeat. The SL/TP loop above runs every
        # cycle regardless of price-alerts; without this line, quiet cycles
        # (no mover) emitted zero INFO logs and the bug-watcher heuristic
        # mis-read the silence as "price monitor dark 5h" (false-positive
        # critical, 2026-06-10). One guaranteed line proves liveness.
        logger.info(
            "Price check done — %d SL/TP alert(s), %d price alert(s)",
            len(sl_tp_alerts), len(price_alerts),
        )
    except Exception as e:
        from runtime.scheduler import is_transient_error
        if is_transient_error(e):
            # Recoverable (Haiku tool-call divergence / rate-limit). Next 15-min
            # cycle retries automatically — no traceback noise, no alert.
            logger.warning("Price check transient error (auto-retry next cycle): %s", e)
        else:
            logger.exception("Price check failed")
