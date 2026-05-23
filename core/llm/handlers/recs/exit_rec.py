"""EXIT recommendation handler + thesis-decay confirmation gate.

Gates: position exists, reason set, thesis-decay confirmed via multi-signal
intraday check (vol_ratio / RSI / VWAP-dev). Hard exits (SL hit, earnings,
TP, panic) always pass through; soft thesis-decay exits need confirmation
to avoid whipsaw on transient weakness.
"""

import logging
from datetime import datetime

import config
from notifier import send_notification as _notify

from core.gate_log import log_gate
from core.portfolio import load_portfolio


logger = logging.getLogger(__name__)


def exit_thesis_decay_confirmed(ticker: str, reason: str, market_data: dict) -> bool:
    """Multi-signal confirmation for thesis-decay exit-recs.

    Hard exits (SL-hit, earnings, TP, panic) always return True. Thesis-decay
    exits need confirmation via three intraday signals — suppression triggers
    if ANY indicates "this is not a real exit-event": thin volume / oversold-
    bounce-zone / mild dip. Bot re-tries next bar if signal persists, so this
    is a soft delay, not a hard block.
    """
    reason_lower = reason.lower()
    HARD_EXIT_MARKERS = (
        "sl-hit", "stop-loss", "stop loss", "sl hit",
        "earnings", "panic", "tp1", "tp2", "take-profit", "take profit",
    )
    if any(m in reason_lower for m in HARD_EXIT_MARKERS):
        return True

    md = market_data.get(ticker) or {}

    vol_ratio = md.get("volume_ratio")
    if isinstance(vol_ratio, (int, float)) and vol_ratio < config.EXIT_GATE_MIN_VOL_RATIO:
        log_gate(ticker, "exit_confirm_volume", True,
                 f"vol_ratio {vol_ratio:.2f} < {config.EXIT_GATE_MIN_VOL_RATIO} — thin selloff, whipsaw-risk",
                 {"vol_ratio": vol_ratio})
        logger.info("Exit suppress %s: vol_ratio %.2f below %.2f",
                    ticker, vol_ratio, config.EXIT_GATE_MIN_VOL_RATIO)
        return False

    rsi = md.get("rsi14")
    if isinstance(rsi, (int, float)) and rsi < config.EXIT_GATE_MIN_RSI:
        log_gate(ticker, "exit_confirm_rsi", True,
                 f"rsi14 {rsi:.1f} < {config.EXIT_GATE_MIN_RSI} — oversold-bounce-zone, wait",
                 {"rsi14": rsi})
        logger.info("Exit suppress %s: rsi %.1f below %.1f",
                    ticker, rsi, config.EXIT_GATE_MIN_RSI)
        return False

    vdev = md.get("vwap_dev_atr")
    if isinstance(vdev, (int, float)) and config.EXIT_GATE_MAX_VWAP_DEV_ATR < vdev < 0:
        log_gate(ticker, "exit_confirm_vwap", True,
                 f"vwap_dev {vdev:+.2f}×ATR mild — not panic-selling, await sustained break",
                 {"vwap_dev_atr": vdev})
        logger.info("Exit suppress %s: vwap_dev %.2f mild", ticker, vdev)
        return False

    return True


def handle_exit_recommendation(exit_rec: dict, market_data: dict) -> dict | None:
    """Exit handler. Returns persisted exit-rec on pass, None on block."""
    et = (exit_rec.get("ticker") or "").upper()
    ereason = (exit_rec.get("reason") or "").strip()[:120]
    eurg = (exit_rec.get("urgency") or "today").lower()
    if eurg not in {"now", "today", "eod"}:
        eurg = "today"
    epf = load_portfolio()
    epos = next(
        (tr for tr in epf.get("open_trades", [])
         if (tr.get("ticker") or "").upper() == et),
        None,
    )
    if not epos:
        log_gate(et, "exit_no_position", True, "recommend_exit without open position", {})
        logger.info("Exit suppressed: %s no open position", et)
        return None
    if not ereason:
        log_gate(et, "exit_no_reason", True, "reason empty", {})
        logger.info("Exit suppressed: %s no reason", et)
        return None
    if not exit_thesis_decay_confirmed(et, ereason, market_data):
        logger.info("Exit suppressed: %s thesis-decay not confirmed", et)
        return None

    orig_entry = float(epos.get("entry_price") or 0)
    orig_size = float(epos.get("size_eur") or 0)
    curr_price = (market_data.get(et) or {}).get("price")
    curr_str = f"€{curr_price:.2f}" if isinstance(curr_price, (int, float)) else "–"
    pnl_pct = (
        ((curr_price - orig_entry) / orig_entry * 100)
        if isinstance(curr_price, (int, float)) and orig_entry > 0 else None
    )
    pnl_str = f"{pnl_pct:+.2f}%" if pnl_pct is not None else "?"
    urgency_emoji = {"now": "🚨", "today": "⚠️", "eod": "🕐"}.get(eurg, "⚠️")
    exit_wkn = config.TR_WKN_MAP.get(et)
    exit_wkn_line = f"\n📱 TR-WKN: `{exit_wkn}`" if exit_wkn else ""
    exit_msg_id = _notify(
        f"🎯 *EXIT* | `{et}` | {urgency_emoji} {eurg}\n"
        f"Grund: {ereason}\n"
        f"Bestand: €{orig_size:.0f} @ €{orig_entry:.2f} | Jetzt: {curr_str} ({pnl_str})"
        f"{exit_wkn_line}\n"
        f"_Reply `/confirm` um auf TR zu schließen, dann `/close {et} @PREIS [#tag]`._"
    )
    log_gate(et, "exit_all_passed", False, "exit recommended", {"urgency": eurg})
    logger.info("Exit recommendation: %s urgency=%s (msg_id=%s)",
                et, eurg, exit_msg_id)
    return {
        "kind": "exit",
        "ticker": et,
        "reason": ereason,
        "urgency": eurg,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending",
        "message_id": exit_msg_id,
    }
