"""Feature flags — boolean toggles for system-wide behavior.

Phase A1: plain constants (current state). Phase A2 will introduce a
FeatureFlag dataclass with expiry + rationale metadata + is_enabled() helper.
For now, callers continue to read these constants directly.
"""

# Red-Team-Pass: zweiter Claude-Call kritisiert eigene Rec im Bear-Modus.
# Blockt wenn confidence_thesis_holds < threshold ODER verdict==KILL.
# Gleicher Modell-Tier wie Hauptcall (Haiku Event/Opening, Sonnet Morning).
RED_TEAM_ENABLED = True

# Auto-split single TP into [1R, original-TP] for partial scale-out.
AUTO_SPLIT_SINGLE_TP_AT_1R = True

# Regime gate — RISK_OFF blocks new LONG entries (conservative full-trust bias).
RISK_OFF_BLOCKS_LONGS = True

# Stock-news pre-gate: skip Claude call when ticker has neither open position
# nor active morning watch_level — no thesis to verify, no position to manage,
# no actionable verdict possible. Set False to restore the CLAUDE.md "filter at
# output, not input" rule (every news headline goes to Claude).
NEWS_REQUIRE_OPEN_OR_WATCH = True
