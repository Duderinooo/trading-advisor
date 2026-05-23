# 2026-04-29 — Watch-Tool truncation (max_tokens cap too low)

## Symptom

Morning briefs silently emitted `set_watch_levels({})` with empty input dict.
For 3 days the bot reported "no setups today" when in fact Sonnet's tool_use
block was truncated mid-stream by `max_tokens=400`. Output_tokens hit the cap;
JSON cut off before `"levels"` key was emitted.

Subsequent regressions hit the same shape:
- **2026-05-07:** opening-mode `max_tokens=500/500` truncated `recommend_entry`
  twice in one day (XETRA + US). Missing fields: `setup_type`, `top_fail_mode`.
- **2026-05-20:** morning `max_tokens=1200` overflowed after the manifest-2
  rewrite (BASE-QUALITY-SCORE + ENTRY-STATE-TAXONOMIE sections + zone_low /
  zone_high schema) — set_watch_levels(9 levels) + recommend_entry serialized
  through richer schema exceeded the cap. malformed_tool_input, zero raw_levels.
- **2026-05-22:** morning bumped 2500 → 3500 for tool_choice="any" + v7
  required-fields (entry_state, primary_signal, why_now) — Sonnet's essay-
  tendency before tool_use needs headroom.

## Root cause

Per-mode `max_tokens` was tuned to "expected" payload size at one point in
time. Schema growth + new fields silently consumed the buffer.

## Fix

Track `output_tokens` per call in `core/llm/telemetry/trace.py` + emit
`truncated=True` + ERROR-log when `stop_reason == "max_tokens"`. Bump cap
generously on every schema/prompt addition.

Current values (in `core/llm/prompt/builder.py` MODE_CONFIG):
- morning: 3500
- opening: 900
- event: 900

## Constants

| File | Constant | Value |
|---|---|---|
| `core/llm/prompt/builder.py` | `MODE_CONFIG["morning"]["max_tokens"]` | 3500 |
| `core/llm/prompt/builder.py` | `MODE_CONFIG["opening"]["max_tokens"]` | 900 |
| `core/llm/prompt/builder.py` | `MODE_CONFIG["event"]["max_tokens"]` | 900 |

## Lessons

- **Tool-use can truncate mid-JSON.** SDK accepts the block; downstream sees
  `None` for required keys.
- **Always log stop_reason + output_tokens.** Without trace, this fails silent.
- **Schema-change checklist:** add field → bump max_tokens budget pro-actively.
- **Required-fields validation:** drop incomplete recs (gate_required_fields)
  instead of persisting them with None.
