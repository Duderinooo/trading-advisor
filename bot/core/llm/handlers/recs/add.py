"""ADD (pyramiding) handler.

Gates: position exists, valid size, current price not drifted >ATR×N from
original entry, current price still above original SL. No drift gate → Claude
would treat any continuation as ADD trigger (fresh entry expected instead).
"""

import logging
from datetime import datetime

import config
from notifier import send_actionable

from core.gate_log import log_gate
from core.portfolio import load_portfolio


logger = logging.getLogger(__name__)


def handle_add_recommendation(add: dict, market_data: dict) -> dict | None:
    """Returns persisted ADD-rec dict on pass, None on block."""
    at = (add.get("ticker") or "").upper()
    add_size = float(add.get("additional_size_eur") or 0)
    add_pf = load_portfolio()
    open_pos = next(
        (tr for tr in add_pf.get("open_trades", [])
         if (tr.get("ticker") or "").upper() == at),
        None,
    )
    if not open_pos:
        log_gate(at, "add_no_position", True, "ADD on ticker without open position", {})
        logger.info("ADD suppressed: %s has no open position", at)
        return None
    if add_size <= 0:
        log_gate(at, "add_invalid_size", True, f"additional_size_eur={add_size}", {})
        logger.info("ADD suppressed: %s invalid size %s", at, add_size)
        return None

    orig_entry = float(open_pos.get("entry_price") or 0)
    orig_size = float(open_pos.get("size_eur") or 0)
    orig_sl = float(open_pos.get("stop_loss") or 0)
    md = market_data.get(at) or {}
    curr = md.get("price")
    atr = md.get("atr14")

    if isinstance(curr, (int, float)) and isinstance(atr, (int, float)) and atr > 0:
        drift = abs(float(curr) - orig_entry)
        if drift > atr * config.ADD_MAX_PRICE_DRIFT_ATR:
            log_gate(at, "add_price_drift", True,
                     f"|curr-orig| {drift:.2f} > {config.ADD_MAX_PRICE_DRIFT_ATR}×ATR ({atr:.2f})",
                     {"curr": curr, "orig": orig_entry, "atr": atr})
            logger.info(
                "ADD suppressed: %s price drift €%.2f > %.1f×ATR (%.2f) — fresh entry expected",
                at, drift, config.ADD_MAX_PRICE_DRIFT_ATR, atr,
            )
            return None

    if isinstance(curr, (int, float)) and orig_sl > 0 and float(curr) <= orig_sl:
        log_gate(at, "add_sl_breached", True,
                 f"price {curr} ≤ original SL {orig_sl}",
                 {"curr": curr, "orig_sl": orig_sl})
        logger.info("ADD suppressed: %s current %.2f ≤ original SL %.2f",
                    at, curr, orig_sl)
        return None

    trigger = (add.get("trigger") or "")[:120]
    reinforce = (add.get("thesis_reinforcement") or "")[:120]
    add_conv = add.get("conviction")
    add_msg_id = send_actionable(
        "ADD", at, f"+€{add_size:.0f}",
        reason=trigger or reinforce or "Setup-Verstärkung",
        conviction=add_conv if isinstance(add_conv, int) else None,
        extras={
            "Verstärkung": reinforce,
            "Bestand": f"€{orig_size:.0f} @ €{orig_entry:.2f}",
            "SL": f"€{orig_sl:.2f}",
        },
    )
    log_gate(at, "add_all_passed", False, "ADD approved",
             {"add_size": add_size, "orig_size": orig_size})
    logger.info("ADD recommendation: %s +€%.2f (msg_id=%s)", at, add_size, add_msg_id)
    return {
        "kind": "add",
        "ticker": at,
        "additional_size_eur": add_size,
        "trigger": trigger,
        "thesis_reinforcement": reinforce,
        "conviction": add_conv,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending",
        "message_id": add_msg_id,
    }
