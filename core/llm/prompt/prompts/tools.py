"""Anthropic tool-schema JSON (L4 of the prompt architecture).

Pure technical schemas — no trading philosophy. Setup-Type vocabulary lives in
core.llm.prompt.prompts.strategy, not here.
"""

import config

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
