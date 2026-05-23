"""Shadow config — what-if values for A/B counterfactual analysis.

This module does NOT change live bot behaviour. Real config values
(MIN_EXPECTED_EDGE etc. in config/risk.py) drive every gate at runtime.
Shadow values are consumed only by analytics functions like
`compute_shadow_what_if(closed_trades, SHADOW_OVERRIDES)` which replay
closed-trade outcomes against the shadow thresholds and report:
- how many trades would have been blocked under shadow
- net PnL impact (skipped losses minus skipped wins)
- equivalent statistic per setup_type

Use this as data-driven evidence before touching the real values:
adjust SHADOW_OVERRIDES, watch the dashboard's Shadow Delta card for
≥20 closed trades, and only then promote a value into config/risk.py
following the CLAUDE.md tuning rules.
"""

# Map: real-config key → shadow value to test. None or missing key →
# no override (treated as "same as real"). Add a key to start tracking;
# remove to stop.
SHADOW_OVERRIDES: dict[str, float] = {
    # Example: tighten the edge gate from the live 0.04 to a stricter 0.06
    # and see how many of the last N trades would have been skipped.
    # "MIN_EXPECTED_EDGE": 0.06,
}
