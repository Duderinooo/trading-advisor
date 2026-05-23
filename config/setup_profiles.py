"""Per-setup-type behavior profiles.

Centralizes the override-rules previously scattered across 4 gate functions +
backtest. Adding a new setup_type = one dict entry instead of 4 edits.

Each profile encodes which deterministic gates a setup family bypasses or
modifies. Live gate logic queries get_profile(setup_type).<flag>.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SetupProfile:
    """How a setup_type modifies the standard entry-gate pipeline."""
    name: str
    rs_override: bool             # True = bypass MIN_RS_20D_VS_INDEX_PCT gate
    min_confluence_offset: int    # add to MIN_CONFLUENCE_SCORE floor (negative = relax)
    requires_breakout_volume: bool  # True = enforce vol_ratio ≥ MIN_BREAKOUT_VOLUME_RATIO
    bypass_earnings_block: bool   # True = ignore T-N earnings hard-block


# Default = strict pipeline. Any setup_type not listed below uses this.
_DEFAULT = SetupProfile(
    name="default",
    rs_override=False,
    min_confluence_offset=0,
    requires_breakout_volume=False,
    bypass_earnings_block=False,
)


# Override family: setups that legitimately enter against the standard trend filter.
# RS-negative + low-confluence are PART of the thesis (catch the bottom, fade
# the extreme, fill the gap). Relaxed confluence floor; RS gate bypassed.
_MEAN_REV_FAMILY = {"rs_override": True, "min_confluence_offset": -2}

SETUP_PROFILES: dict[str, SetupProfile] = {
    "mean_reversion": SetupProfile(
        "mean_reversion", **_MEAN_REV_FAMILY,
        requires_breakout_volume=False, bypass_earnings_block=False,
    ),
    "reversal_oversold": SetupProfile(
        "reversal_oversold", **_MEAN_REV_FAMILY,
        requires_breakout_volume=False, bypass_earnings_block=False,
    ),
    "gap_fill": SetupProfile(
        "gap_fill", **_MEAN_REV_FAMILY,
        requires_breakout_volume=False, bypass_earnings_block=False,
    ),
    "pre_breakout_squeeze": SetupProfile(
        "pre_breakout_squeeze", **_MEAN_REV_FAMILY,
        requires_breakout_volume=False, bypass_earnings_block=False,
    ),
    "breakout_resistance": SetupProfile(
        "breakout_resistance",
        rs_override=False, min_confluence_offset=0,
        requires_breakout_volume=True,   # see research/2026-05-07-breakout-volume-floor.md
        bypass_earnings_block=False,
    ),
    "earnings_drift": SetupProfile(
        "earnings_drift",
        rs_override=False, min_confluence_offset=0,
        requires_breakout_volume=False,
        bypass_earnings_block=True,  # post-earnings drift T+1+ explicitly allowed
    ),
    # Trend-family defaults — explicit so adding a new trend setup is one line:
    "breakout_long": SetupProfile(
        "breakout_long", False, 0, False, False,
    ),
    "support_bounce": SetupProfile(
        "support_bounce", False, 0, False, False,
    ),
    "pullback_ma20": SetupProfile(
        "pullback_ma20", False, 0, False, False,
    ),
    "pullback_ma50": SetupProfile(
        "pullback_ma50", False, 0, False, False,
    ),
    "flag_continuation": SetupProfile(
        "flag_continuation", False, 0, False, False,
    ),
}


def get_profile(setup_type: str | None) -> SetupProfile:
    """Lookup profile by setup_type (case-insensitive). Unknown → _DEFAULT."""
    if not setup_type:
        return _DEFAULT
    return SETUP_PROFILES.get(setup_type.lower(), _DEFAULT)
