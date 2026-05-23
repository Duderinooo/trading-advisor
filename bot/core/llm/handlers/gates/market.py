"""Market-wide risk blocks — gates that fail the entire request regardless of ticker.

risk_halt:        kill-switch / daily loss cap / drawdown / heat-cap
regime:           RISK_OFF + LONG (configurable)
no_entry_zone:    open/close noise windows
extended_up_day:  chase-protection for parabolic intraday moves
"""

import logging
from datetime import datetime

import config
from core.gate_log import log_gate
from core.portfolio import risk_halt_status
from core.llm.handlers.gates.context import GateContext
from notifier import send_notification as _notify


logger = logging.getLogger(__name__)


def gate_risk_halt(entry: dict, ctx: GateContext) -> bool:
    """Halt = kill-switch / daily-loss cap / drawdown latch / portfolio-heat cap."""
    halt = risk_halt_status(ctx.portfolio)
    if not halt["halt"]:
        return True
    reason = " | ".join(halt["reasons"])
    logger.warning("Entry BLOCKED by risk halt: %s", reason)
    log_gate(ctx.ticker, "risk_halt", True, reason, halt.get("metrics"))
    if "risk_halt" in config.GATE_BLOCK_NOTIFY_WHITELIST:
        _notify(f"⛔ *ENTRY BLOCKIERT* ({ctx.ticker})\n{reason}")
    return False


def gate_regime(entry: dict, ctx: GateContext) -> bool:
    """RISK_OFF + LONG block (configurable via RISK_OFF_BLOCKS_LONGS)."""
    if not (config.RISK_OFF_BLOCKS_LONGS and ctx.regime.startswith("RISK_OFF")):
        return True
    direction = str(entry.get("direction") or "LONG").upper()
    if direction != "LONG":
        return True
    logger.warning("Entry BLOCKED by regime gate: RISK_OFF + LONG")
    log_gate(ctx.ticker, "regime", True,
             f"RISK_OFF + LONG (regime={ctx.regime})", {"regime": ctx.regime})
    return False


def gate_no_entry_zone(entry: dict, ctx: GateContext) -> bool:
    """Block during auction/EOD noise windows. Auction spikes + EOD chop = bad
    fills + lag-amplified slippage on 15min-delayed data."""
    now = datetime.now()
    now_min = now.hour * 60 + now.minute
    for sh, sm, eh, em in config.NO_ENTRY_WINDOWS:
        if sh * 60 + sm <= now_min < eh * 60 + em:
            window = f"{sh:02d}:{sm:02d}–{eh:02d}:{em:02d}"
            logger.warning("Entry BLOCKED by no-entry-zone: %s in window %s",
                           ctx.ticker, window)
            log_gate(ctx.ticker, "no_entry_zone", True,
                     f"window {window}", {"window": window})
            return False
    return True


def gate_extended_up_day(entry: dict, ctx: GateContext) -> bool:
    """Block when today's gain exceeds 1.5×ATR. Chase-protection — entering AFTER
    1.5 ATR of upside is statistically a pullback setup that hits any reasonable SL.
    Asymmetric: only UP-extended blocked. DOWN-extended remains swing-entry candidate."""
    change_pct = ctx.snap_md.get("change_pct")
    atr_pct = ctx.snap_md.get("atr14_pct")
    if not (isinstance(change_pct, (int, float))
            and isinstance(atr_pct, (int, float)) and atr_pct > 0):
        return True
    if change_pct <= 1.5 * atr_pct:
        return True
    logger.warning(
        "Entry BLOCKED by extended-UP-day gate: %s change=%+.2f%% > 1.5×ATR%%=%.2f%%",
        ctx.ticker, change_pct, 1.5 * atr_pct,
    )
    log_gate(ctx.ticker, "extended_up_day", True,
             f"change_pct {change_pct:+.2f}% > 1.5×atr14_pct ({1.5 * atr_pct:.2f}%)",
             {"change_pct": change_pct, "atr14_pct": atr_pct,
              "threshold_pct": 1.5 * atr_pct})
    return False
