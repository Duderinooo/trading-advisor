"""Mode-specific behavioral-delta prompts (L3 of the prompt architecture).

Concatenated to STRATEGY_SYSTEM in analyze_portfolio per mode.
"""

from core.llm.prompt.prompts.strategy import EXIT_RULES


_TOOL_ONLY_BANNER = """tool_calls only.
NO markdown.
NO explanations.
NO prose.
NO reasoning.
ONLY valid tool_calls."""


MORNING_PREP_PROMPT = f"""morning:
{_TOOL_ONLY_BANNER}

MANDATORY: emit a recommend_entry tool_call for EVERY qualifying setup
(conv>=3, rr>=2) — up to 5. One tool_call PER setup. NEVER describe a
qualifying entry in prose instead of calling the tool — prose-only =
dropped = not actioned. If you name a setup as buyable, you MUST emit its
recommend_entry. Build each fully (entry/SL/TP/size/thesis/setup_type).
set_watch_levels: defense_only for open_positions.
submit_pass only when literally 0 qualifying setups & 0 open."""


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
