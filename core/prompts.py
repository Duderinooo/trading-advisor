"""All prompt strings + Anthropic tool schemas.

4-Layer-Architektur v8 (2026-05-22):
- L1 CORE_PHILOSOPHY: Mission + Worldview (DSL-style rules)
- L2 EXECUTION_RULES: Mechanik + Numbers via shared constants
- L3 MODE_PROMPTS: nur Behavioral-Delta pro Mode
- L4 TOOL_SCHEMAS: pure tech, keine Trading-Philosophie

STRATEGY_SYSTEM = L1 + L2 + Excluded-Tickers + Section-Legend (cached).
Mode-specific prompts (L3) werden im User-Message konkateniert.
"""

import config


# ============================================================================
# SHARED RULE CONSTANTS — single source of truth, composed via f-string
# ============================================================================

ENTRY_STATE_RULES = """entry_state (4 mutually exclusive states):
EARLY := base_quality_score in [4,6] & repair_signals_active & entry_price<live_price
VALID := base_quality_score>=6 & rr>=2 & structural_sl
LATE := change_pct>1.2*atr14_pct | perf_1_2d>=0.05 | news_first_candle | gap>0.05
EXTENDED := pct_below_52w_high>-0.02 | rsi>75 | up_days_streak>=3 | v_recovery_no_base | relief_bounce_8_15_no_consolidation

late_pullback_override := entry_price<=live_price-1*atr14
LATE only tradeable if late_pullback_override holds.
EXTENDED never tradeable until pullback resets state."""


LONG_FEATURES = [
    "base_quality_score>=4",
    "base_quality_items.selling_exhaustion",
    "base_quality_items.atr_contraction",
    "base_quality_items.failed_breakdown_reclaim",
    "higher_lows_5d>=2",
    "range_compression<0.5",
    "-0.25<=pct_below_52w_high<=-0.08",
    "rs_20d_vs_index_pct>0",
    "reclaim_ma20|ma50|pivot",
]


RED_FLAGS = [
    "rs_20d_vs_index_pct<0",
    "base_quality_score<4",
    "analyst_upside_pct<0 | rec_key in {underperform,sell}",
]
# 3+ red_flags → PASS.


CONVICTION_MAP = """conviction (deterministic from quality + rr):
CONV_5 := quality>=7 & rr>=3
CONV_4 := quality>=6 & rr>=2.5
CONV_3 := quality>=4 & rr>=2
conv<=2 → PASS

p_win := {5:0.70, 4:0.60, 3:0.55}
hit_rate_haircut subtracted, floor 0.55."""


QUALITY_FAMILY_RULES = """setup_family quality:
swing_low_family := {support_bounce, pre_breakout_squeeze, reversal_oversold, mean_reversion, gap_fill, pullback_ma20, pullback_ma50}
- primary base_quality_score: >=7=A+, 4-6=building, <4=PASS
- confluence secondary, floor 2

trend_family := {breakout_resistance, flag_continuation, earnings_drift}
- primary confluence_score: >=7=full, 5-6=half, <5=PASS
- base_quality secondary

swing_low edge = structure_repair (base_quality_items), not momentum."""


ANALYST_RULES = """analyst (tie-breaker, never primary):
strong_buy|buy & upside>=0.10 → +1 conv, max 5
underperform|sell | upside<0 → no_long
count<5 → ignore"""


EXIT_RULES = """exit_only:
- thesis_break
- confirmed_reject := macd_crossdown | bb_mid_lost | vol_distribution
- bearish_divergence & lower_high

not_exit:
- tp_miss
- profit_lock_only → update_position_targets"""


SETUP_TYPES_RULES = """setup_type (required, single dominant tag):
pullback_ma20|pullback_ma50 → entry at MA
pre_breakout_squeeze → range_compression<0.5 & range_top & rising_vol. entry in base. SL<range_low. preferred over breakout_resistance.
support_bounce → entry at support
reversal_oversold → rsi<30 & hammer & support. entry at low.
mean_reversion → entry at deviation_extreme. rs_gate_override_ok.
gap_fill → entry at gap_edge
flag_continuation → entry in flag
earnings_drift → T+1..T+5 after strong_beat
breakout_resistance → DEMOTED. only scale_in | special_catalyst."""


TOOL_ROLES = """tool_roles:
recommend_entry: limit_buy new position
recommend_add_to_position: pyramid existing (stronger thesis & price<=1*atr from entry & sl_valid)
update_position_targets: discretionary SL/TP. not on thesis_break.
recommend_exit: see exit_only.
set_watch_levels: defense_only | real breakout_long with vol+close_confirm.
submit_pass: when no action fits."""


# ============================================================================
# LAYER 1 — CORE PHILOSOPHY
# ============================================================================

CORE_PHILOSOPHY = f"""asymmetric_swing_structure_filter. €1000. trade_republic. long_only.

role: structure_filter + thesis_builder. tool_calls only.
edge: 15min_lag + manual_tr_execution = structure not speed. hold 2-10d.
default := PASS. cash := active_position.
if rr>=2 & structural_sl & quality>=4: trade > cash.

priority:
1. entry_state
2. setup_family quality
3. catalyst_refinement

state_keys: setup_family, entry_state, quality_tier

{ENTRY_STATE_RULES}

long_features (active search, structure not momentum):
{chr(10).join("- " + f for f in LONG_FEATURES)}

red_flags:
{chr(10).join("- " + r for r in RED_FLAGS)}
3+ red_flags → PASS.

news := catalyst flipping base→swing. never signal for already_run_candle.
no_shorts. bearish = inverse_etf_long | cash."""


# ============================================================================
# LAYER 2 — EXECUTION RULES
# ============================================================================

EXECUTION_RULES = f"""EXECUTION RULES (LLM decisions — engine constraints separate).

{QUALITY_FAMILY_RULES}

{CONVICTION_MAP}

RR + SL + TP:
- rr_floor=2 (hard)
- sl structural (engine clamps to atr-range automatically)
- tp multistage on resistance cluster

LIMIT_BUY:
- entry_price = limit_buy intent
- entry_price<=live_price preferred (structural entry: ma_tap/support/range_low/bb_lower)
- rr@live_price<2 → entry_price lower, NOT watch
- entry_price>live_price only for real breakout_resistance confirm

{ANALYST_RULES}

PRE_MORTEM (top_fail_mode required):
support_breakdown / thesis_invalidation / earnings_miss / macro_event / regime_shift / sector_rotation / false_breakout / stop_run
no_clear_fail_mode → PASS

TICKER_PREFERENZ:
- ideal: €10-80, atr14_pct 1.5-4%, clean bases
- avoid: >€80 (whole_share trifft), atr>6%, hypervolatile_no_structure
- multi_r_upside (1:3+) preferred

{SETUP_TYPES_RULES}

{EXIT_RULES}

{TOOL_ROLES}"""

# Backwards compat — analyzer.py imports STRATEGY_PROMPT
STRATEGY_PROMPT = CORE_PHILOSOPHY + "\n\n" + EXECUTION_RULES


# Excluded tickers static across runs → fold into cached system prompt.
_EXCLUDED_SUFFIX = (
    f"\n\nAUSGESCHLOSSEN (NIE traden): {', '.join(config.EXCLUDED_TICKERS)}"
    if config.EXCLUDED_TICKERS else ""
)

_SECTION_LEGEND = f"""

context_sections (user_message fields):
- ## HIT-RATE: brier 0=perfect 0.25=random. KORREKTUR subtracts haircut, floor 0.55. setup×regime: bad combo → higher conv. kelly_mult ∈ [0.10, 0.50] → size scaling. pre_mortem_accuracy<0.50 → broaden fail_modes.
- ## LAST-20 MISTAKES (prediction/timing/execution/external): dominant class → counter-steer.
- ## PORTFOLIO HEAT: <0.30 budget → conv=5 only. <0.10 → PASS.
- ## HEUTE: HIGH-IMPACT MACRO: pre-release no new entries (unless event-independent).
- ## GAPS: tickers move>={config.GAP_FLAG_PERCENT}%."""

STRATEGY_SYSTEM = STRATEGY_PROMPT + _EXCLUDED_SUFFIX + _SECTION_LEGEND


# ============================================================================
# LAYER 3 — MODE PROMPTS (Behavioral delta only)
# ============================================================================

_TOOL_ONLY_BANNER = """tool_calls only.
NO markdown.
NO explanations.
NO prose.
NO reasoning.
ONLY valid tool_calls."""


MORNING_PREP_PROMPT = f"""morning:
{_TOOL_ONLY_BANNER}

per A+ setup (conv>=3, rr>=2): recommend_entry. 0-5 calls.
set_watch_levels: defense_only for open_positions.
submit_pass when 0 setups & 0 open."""


OPENING_CHECK_PROMPT = f"""open_check:
{_TOOL_ONLY_BANNER}

delta after market_open. default := submit_pass.
recommend_exit | recommend_entry | recommend_add_to_position on action.
no set_watch_levels."""


EVENT_TRIGGER_PROMPT = f"""event:
{_TOOL_ONLY_BANNER}

role := defender + news_catalyst.
defender → open_positions: thesis_degradation, exit_triggers, invalidate_watch_hits.
news_catalyst → recommend_entry only on real news (buyback, earnings_beat_surprise, m&a, fresh strong_buy upgrade).

not: watch_hits_without_open_pos, re_reasoning_technical_setups, setups_from_pure_price_moves.

{EXIT_RULES}"""


# ============================================================================
# LAYER 4 — TOOL SCHEMAS (pure tech, no philosophy)
# ============================================================================

WATCH_LEVELS_TOOL = {
    "name": "set_watch_levels",
    "description": "Watch-levels (defense + breakout_confirm). Merge-by-ticker.",
    "input_schema": {
        "type": "object",
        "properties": {
            "levels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": [
                                "breakout_long",
                                "support_bounce",
                                "resistance_reject",
                                "inverse_etf_entry",
                                "accumulation_zone",
                            ],
                        },
                        "trigger_price": {"type": "number"},
                        "zone_low": {"type": "number"},
                        "zone_high": {"type": "number"},
                        "thesis": {"type": "string", "maxLength": 120},
                        "invalidate_below": {"type": "number"},
                        "confirm_close_above": {"type": "number"},
                        "min_volume_ratio": {"type": "number"},
                        "valid_until": {"type": "string"},
                        "trailing_stop_pct": {"type": "number"},
                        "note": {"type": "string", "maxLength": 80},
                    },
                    "required": ["ticker", "type", "trigger_price", "thesis"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["levels"],
        "additionalProperties": False,
    },
}


RED_TEAM_SYSTEM = """Bear critic.

Goal: destroy ONE core bull assumption with measurable evidence.

AUTO_KILL:
- thesis_invalidated
- confidence<0.40
- EXTENDED/LATE override
- rr<1.5 after recalc
- regime_conflict (risk_off+long ohne reversal_exception)

Prefer APPROVE or KILL. WEAKEN only for partial degradation.

Tool-call mandatory."""


RED_TEAM_TOOL = {
    "name": "submit_critique",
    "description": "Bear-case review. Mandatory tool-call.",
    "input_schema": {
        "type": "object",
        "properties": {
            "top_failure_modes": {
                "type": "array",
                "items": {"type": "string", "maxLength": 100},
                "minItems": 1,
                "maxItems": 3,
            },
            "confidence_thesis_holds": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "verdict": {"type": "string", "enum": ["APPROVE", "WEAKEN", "KILL"]},
            "reason": {"type": "string", "maxLength": 140},
        },
        "required": ["top_failure_modes", "confidence_thesis_holds", "verdict", "reason"],
        "additionalProperties": False,
    },
    "cache_control": {"type": "ephemeral"},
}


UPDATE_TARGETS_TOOL = {
    "name": "update_position_targets",
    "description": "Discretionary SL/TP update on running position.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "new_stop_loss": {"type": "number"},
            "new_take_profit": {
                "type": ["array", "number"],
                "items": {"type": "number"},
            },
            "reason": {"type": "string", "maxLength": 120},
        },
        "required": ["ticker", "reason"],
        "additionalProperties": False,
    },
}


SUBMIT_PASS_TOOL = {
    "name": "submit_pass",
    "description": "When no action fits.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "maxLength": 120},
        },
        "required": ["reason"],
        "additionalProperties": False,
    },
    "cache_control": {"type": "ephemeral"},
}


RECOMMEND_EXIT_TOOL = {
    "name": "recommend_exit",
    "description": "Exit rec for open position.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "reason": {"type": "string", "maxLength": 120},
            "urgency": {"type": "string", "enum": ["now", "today", "eod"]},
        },
        "required": ["ticker", "reason", "urgency"],
        "additionalProperties": False,
    },
    "cache_control": {"type": "ephemeral"},
}


RECOMMEND_ADD_TOOL = {
    "name": "recommend_add_to_position",
    "description": "Pyramid into open position.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "additional_size_eur": {"type": "number"},
            "trigger": {"type": "string", "maxLength": 100},
            "thesis_reinforcement": {"type": "string", "maxLength": 100},
            "conviction": {"type": "integer", "minimum": 3, "maximum": 5},
        },
        "required": ["ticker", "additional_size_eur", "trigger", "thesis_reinforcement", "conviction"],
        "additionalProperties": False,
    },
}


RECOMMEND_ENTRY_TOOL = {
    "name": "recommend_entry",
    "description": "Limit-buy intent for new position.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "entry_price": {"type": "number"},
            "stop_loss": {"type": "number"},
            "take_profit": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 1,
            },
            "size_eur": {"type": "number"},
            "conviction": {"type": "integer", "minimum": 3, "maximum": 5},
            "p_win": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "hold_days_min": {"type": "integer"},
            "hold_days_max": {"type": "integer"},
            "thesis": {"type": "string", "maxLength": 100},
            "trailing_stop_pct": {"type": "number"},
            "entry_state": {"type": "string", "enum": ["EARLY", "VALID"]},
            "primary_signal": {"type": "string", "maxLength": 80},
            "why_now": {"type": "string", "maxLength": 100},
            "decision_version": {"type": "string"},
            "setup_type": {
                "type": "string",
                "enum": [
                    "pullback_ma20", "pullback_ma50",
                    "breakout_resistance",
                    "pre_breakout_squeeze",
                    "reversal_oversold",
                    "flag_continuation",
                    "support_bounce",
                    "mean_reversion",
                    "gap_fill",
                    "earnings_drift",
                ],
            },
            "top_fail_mode": {
                "type": "string",
                "enum": [
                    "support_breakdown",
                    "thesis_invalidation",
                    "earnings_miss",
                    "macro_event",
                    "regime_shift",
                    "sector_rotation",
                    "false_breakout",
                    "stop_run",
                ],
            },
        },
        "required": ["ticker", "entry_price", "stop_loss", "take_profit", "size_eur",
                     "conviction", "p_win", "thesis", "setup_type", "top_fail_mode",
                     "entry_state", "primary_signal", "why_now"],
        "additionalProperties": False,
    },
}
