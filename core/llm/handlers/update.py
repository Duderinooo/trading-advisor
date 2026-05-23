"""UPDATE_POSITION_TARGETS handler.

Gates: position exists, new value set, reason given, SL stays below entry +
only moves UP (loosening forbidden), TP stays above entry. No fee gate — SL/TP
changes don't fire fees.
"""

import logging
from datetime import datetime

from notifier import send_notification as _notify

from core.gate_log import log_gate
from core.portfolio import load_portfolio


logger = logging.getLogger(__name__)


def handle_update_targets(upd: dict) -> dict | None:
    """SL/TP update handler. Returns persisted update-rec on pass, None on block."""
    ut = (upd.get("ticker") or "").upper()
    new_sl = upd.get("new_stop_loss")
    new_tp = upd.get("new_take_profit")
    ureason = (upd.get("reason") or "").strip()[:120]
    upf = load_portfolio()
    upos = next(
        (tr for tr in upf.get("open_trades", [])
         if (tr.get("ticker") or "").upper() == ut),
        None,
    )
    if not upos:
        log_gate(ut, "update_no_position", True,
                 "update_position_targets without open position", {})
        logger.info("Update suppressed: %s no open position", ut)
        return None
    if new_sl is None and new_tp is None:
        log_gate(ut, "update_empty", True, "neither SL nor TP set", {})
        logger.info("Update suppressed: %s neither SL nor TP set", ut)
        return None
    if not ureason:
        log_gate(ut, "update_no_reason", True, "reason empty", {})
        logger.info("Update suppressed: %s no reason", ut)
        return None

    orig_entry = float(upos.get("entry_price") or 0)
    orig_sl = float(upos.get("stop_loss") or 0)

    if new_sl is not None:
        new_sl = float(new_sl)
        # SL only moves UP. Loosening protection = forbidden via Claude rec.
        if new_sl >= orig_entry:
            log_gate(ut, "update_sl_above_entry", True,
                     f"new_sl {new_sl} >= entry {orig_entry}",
                     {"new_sl": new_sl, "entry": orig_entry})
            logger.info("Update suppressed: %s SL above entry", ut)
            return None
        if orig_sl > 0 and new_sl < orig_sl:
            log_gate(ut, "update_sl_lowered", True,
                     f"new_sl {new_sl} < orig_sl {orig_sl} — loosening forbidden",
                     {"new_sl": new_sl, "orig_sl": orig_sl})
            logger.info("Update suppressed: %s SL lowered (loosening forbidden)", ut)
            return None

    tp_norm = None
    if new_tp is not None:
        tp_norm = new_tp if isinstance(new_tp, list) else [new_tp]
        tp_norm = [float(x) for x in tp_norm]
        if any(x <= orig_entry for x in tp_norm):
            log_gate(ut, "update_tp_below_entry", True,
                     f"tp {tp_norm} has value ≤ entry {orig_entry}",
                     {"new_tp": tp_norm, "entry": orig_entry})
            logger.info("Update suppressed: %s TP ≤ entry", ut)
            return None

    orig_tp = upos.get("take_profit")
    orig_tp_str = (
        " / ".join(f"€{t:.2f}" for t in orig_tp) if isinstance(orig_tp, list)
        else (f"€{orig_tp:.2f}" if isinstance(orig_tp, (int, float)) else "–")
    )
    new_tp_str = (
        " / ".join(f"€{t:.2f}" for t in tp_norm) if tp_norm
        else "(unverändert)"
    )
    new_sl_str = f"€{new_sl:.2f}" if new_sl is not None else "(unverändert)"
    orig_sl_str = f"€{orig_sl:.2f}" if orig_sl > 0 else "–"
    upd_msg_id = _notify(
        f"🔧 *UPDATE* | `{ut}`\n"
        f"Grund: {ureason}\n"
        f"SL: {orig_sl_str} → {new_sl_str}\n"
        f"TP: {orig_tp_str} → {new_tp_str}\n"
        f"_Reply `/confirm` um zu übernehmen oder `/cancel`._"
    )
    log_gate(ut, "update_all_passed", False, "SL/TP update approved",
             {"new_sl": new_sl, "new_tp": tp_norm})
    logger.info("Update recommendation: %s SL=%s TP=%s (msg_id=%s)",
                ut, new_sl, tp_norm, upd_msg_id)
    return {
        "kind": "update",
        "ticker": ut,
        "new_stop_loss": new_sl,
        "new_take_profit": tp_norm,
        "reason": ureason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending",
        "message_id": upd_msg_id,
    }
