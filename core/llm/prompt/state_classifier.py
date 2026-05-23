"""State classification — Python derives categorical states that LLM previously
classified from raw snapshot. Reduces LLM drift + saves tokens.

Outputs attach to market_data[ticker]['state'] dict and are referenced by LLM as
state.entry_state / state.quality_tier / state.regime / state.extension_risk.
"""

import config


def classify_entry_state(snap: dict) -> str:
    """Return EARLY | VALID | LATE | EXTENDED | UNKNOWN.

    Same definitions as ENTRY_STATE_RULES in prompts.py — single source of truth,
    deterministic. LLM no longer re-derives.
    """
    if not isinstance(snap, dict):
        return "UNKNOWN"

    bq = snap.get("base_quality_score") or 0
    change_pct = snap.get("change_pct") or 0
    atr_pct = snap.get("atr14_pct") or 0
    pct_below_high = snap.get("pct_below_52w_high")
    rsi = snap.get("rsi14")
    perf_5d = snap.get("perf_5d_pct")
    bq_items = snap.get("base_quality_items") or {}

    # EXTENDED — parabolic / ATH / overbought
    if isinstance(pct_below_high, (int, float)) and pct_below_high > -2.0:
        return "EXTENDED"
    if isinstance(rsi, (int, float)) and rsi > 75:
        return "EXTENDED"

    # LATE — already-run move
    if (isinstance(atr_pct, (int, float)) and atr_pct > 0
            and isinstance(change_pct, (int, float))
            and change_pct > 1.2 * atr_pct):
        return "LATE"
    if isinstance(perf_5d, (int, float)) and perf_5d >= 5.0:
        return "LATE"

    # VALID — mature swing-low
    if bq >= 6:
        return "VALID"

    # EARLY — base building
    repair_signals = sum([
        bool(bq_items.get("selling_exhaustion")),
        bool(bq_items.get("atr_contraction")),
        bool(bq_items.get("failed_breakdown_reclaim")),
    ])
    if bq >= 4 or repair_signals >= 2:
        return "EARLY"

    return "UNKNOWN"


def classify_quality_tier(snap: dict) -> str:
    """A | B | C | D quality tier based on base_quality_score primarily."""
    if not isinstance(snap, dict):
        return "D"
    bq = snap.get("base_quality_score") or 0
    if bq >= 7:
        return "A"
    if bq >= 5:
        return "B"
    if bq >= 3:
        return "C"
    return "D"


def detect_extension_risk(snap: dict) -> bool:
    """True if ticker is at risk of imminent reversal due to parabolic move."""
    if not isinstance(snap, dict):
        return False
    rsi = snap.get("rsi14")
    pct_below = snap.get("pct_below_52w_high")
    if isinstance(rsi, (int, float)) and rsi > 75:
        return True
    if isinstance(pct_below, (int, float)) and pct_below > -2.0:
        return True
    return False


def detect_late_pullback_override(snap: dict) -> bool:
    """For LATE entries: is a structural pullback (≥1×ATR below live) reachable?

    Used by LLM to decide if LATE setup is still tradeable via limit-buy.
    """
    if not isinstance(snap, dict):
        return False
    price = snap.get("price")
    atr = snap.get("atr14")
    ma20 = snap.get("ma20")
    ma50 = snap.get("ma50")
    if not isinstance(price, (int, float)) or not isinstance(atr, (int, float)):
        return False
    threshold = price - atr
    # True if either MA20 or MA50 lies at/below threshold (pullback target exists).
    for ma in (ma20, ma50):
        if isinstance(ma, (int, float)) and ma <= threshold:
            return True
    return False


def classify_red_flags(snap: dict) -> list[str]:
    """Return list of red-flag names that fire on this ticker.

    LLM rule: 3+ red_flags → PASS.
    """
    flags = []
    if not isinstance(snap, dict):
        return flags
    rs = snap.get("rs_20d_vs_index_pct")
    bq = snap.get("base_quality_score") or 0
    analyst_upside = snap.get("analyst_upside_pct")
    rec_key = (snap.get("analyst_rec_key") or "").lower()

    if isinstance(rs, (int, float)) and rs < 0:
        flags.append("weak_rs")
    if bq < 4:
        flags.append("low_quality")
    if isinstance(analyst_upside, (int, float)) and analyst_upside < 0:
        flags.append("bearish_analyst")
    elif rec_key in {"underperform", "sell", "strong_sell"}:
        flags.append("bearish_analyst")
    return flags


def classify_setup_family(setup_type: str | None) -> str:
    """Map setup_type → setup_family for quality-scoring rules.

    swing_low: base_quality dominant.
    trend: confluence dominant.
    """
    if not setup_type:
        return "unknown"
    swing_low = {
        "support_bounce", "pre_breakout_squeeze", "reversal_oversold",
        "mean_reversion", "gap_fill", "pullback_ma20", "pullback_ma50",
    }
    trend = {"breakout_resistance", "flag_continuation", "earnings_drift"}
    if setup_type in swing_low:
        return "swing_low"
    if setup_type in trend:
        return "trend"
    return "unknown"


def classify_state(snap: dict, regime: str) -> dict:
    """Bundle all classifications for one ticker snapshot.

    Attaches to market_data[ticker]['state'] in analyzer before LLM call.
    Compressed dict — only categorical labels, not raw values. LLM references
    state.entry_state etc. instead of re-computing from raw indicators.
    """
    return {
        "entry_state": classify_entry_state(snap),
        "quality_tier": classify_quality_tier(snap),
        "regime": regime,
        "extension_risk": detect_extension_risk(snap),
        "late_pullback_override": detect_late_pullback_override(snap),
        "red_flags": classify_red_flags(snap),
    }
