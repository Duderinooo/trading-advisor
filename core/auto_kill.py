"""Macro shock auto-kill: flip kill-switch when VIX or SPX cross hard thresholds.

Fires once per shock — only flips kill-switch ON when state crosses the line and
the kill-switch is currently OFF (won't override manual kill, won't oscillate).
User reviews + manually clears via /resume on Telegram.
"""

import logging
from datetime import datetime

import config
from core.data.market_data import get_market_data
from core.portfolio import (
    portfolio_lock, load_portfolio, save_portfolio, kill_switch_active,
)

logger = logging.getLogger(__name__)

# Auto-kill thresholds
VIX_AUTO_KILL = 35.0          # extreme vol — VIX>30 already dampens size; >35 = shutdown
SPX_DROP_AUTO_KILL = 3.0      # |intraday %| ≥ 3 → flip


def check_macro_shock() -> dict | None:
    """Pull SPY+VIX, return reason dict if a threshold breached. Else None."""
    try:
        ctx = get_market_data(["SPY5.DE", "^VIX"])
    except Exception as e:
        logger.warning("auto_kill market pull failed: %s", e)
        return None

    vix = (ctx.get("^VIX") or {}).get("price")
    spy = ctx.get("SPY5.DE") or {}
    spy_chg = spy.get("change_pct")

    if isinstance(vix, (int, float)) and vix >= VIX_AUTO_KILL:
        return {"trigger": "VIX_EXTREME", "vix": vix,
                "reason": f"VIX {vix:.1f} ≥ {VIX_AUTO_KILL}"}
    if isinstance(spy_chg, (int, float)) and spy_chg <= -SPX_DROP_AUTO_KILL:
        return {"trigger": "SPX_GAP_DOWN", "spx_change_pct": spy_chg,
                "reason": f"SPX {spy_chg:+.2f}% ≤ -{SPX_DROP_AUTO_KILL}%"}
    return None


def maybe_auto_kill() -> dict | None:
    """Check shock, flip kill-switch if needed. Returns event dict if flipped, else None."""
    with portfolio_lock:
        pf = load_portfolio()
        if kill_switch_active(pf):
            return None  # already off — don't overwrite manual reason
        # Don't re-fire same trigger within 6h (avoids retrigger after manual clear)
        last = pf.get("auto_kill_last")
        if last:
            try:
                last_ts = datetime.strptime(last["ts"], "%Y-%m-%d %H:%M:%S")
                if (datetime.now() - last_ts).total_seconds() < 6 * 3600:
                    return None
            except (KeyError, ValueError):
                pass

    shock = check_macro_shock()
    if not shock:
        return None

    with portfolio_lock:
        pf = load_portfolio()
        if kill_switch_active(pf):
            return None
        pf["kill_switch"] = True
        pf["kill_switch_reason"] = f"AUTO: {shock['reason']}"
        pf["kill_switch_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        pf["auto_kill_last"] = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **shock,
        }
        save_portfolio(pf)
    logger.warning("AUTO-KILL fired: %s", shock["reason"])
    return shock
