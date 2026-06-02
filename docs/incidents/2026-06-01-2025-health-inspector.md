# Health Report 2026-06-01T20:24:32+02:00

## Overall: 🟡 degraded
One haiku event call generated 8,881 output tokens — 10× the 900-token budget — indicating runaway generation. All other signals clean.

## Pipeline freshness
Heartbeat age=1s. All four trace modes fired today (morning, opening-XETRA, opening-US, event). `api_calls_today=7` consistent with no open trades + all-PASS day.

## LLM channel
**Token overflow on 14:51 event call:**
```
TRACE [event]: out_tok=8881/900 truncated=False text_preview='Geo catalyst noted (Russia fuel shortage). 3OIL.MI violates avoidance...'
```
Haiku produced 8,881 output tokens vs 900-token budget. `truncated=False` means API didn't cut it — the budget is likely a soft warning threshold, not a hard limit passed to the API. Claude wrote extended prose instead of tool-calling, and the response wasn't suppressed.

**stop=tool_use / tool_called=False pattern** across all event + opening traces:
```
TRACE [event  14:20]: tool_called=False stop=tool_use out_tok=1661/900
TRACE [event  14:51]: tool_called=False stop=tool_use out_tok=8881/900
TRACE [opening 15:36]: tool_called=False stop=tool_use out_tok=2894/900
```
Stop reason `tool_use` with no tool registered is contradictory. Either the trace logging doesn't count non-rec tools (fine) or tool dispatch silently failed (not fine). The runner logs show `actions=1` for each, suggesting at least one action completed — consistent with a meta-tool or final-answer tool that `tool_called` doesn't track.

## Gate behavior
`gate_weekly_trend`: FNR=1.0 on N=1 block (one blocked trade would have won). Below `MIN_SAMPLE_SIZE_FOR_TUNING=20` — not actionable for tuning, but log it.

## Anomalies
- `stderr` full of `MallocStackLogging: can't turn off...` — benign macOS Python subprocess noise from subagent PIDs. No action needed.
- GEO dedup working correctly: 3OIL.MI fired once at 14:50 (Russia fuel/Iran talks collapse), then ~20 subsequent articles suppressed. Single haiku call absorbed the whole Iran-war news cycle.

## Recommended actions

1. **Investigate 8,881-token haiku event call** — check whether `max_tokens` is being passed to the Anthropic API for haiku event calls. If the 900-token budget is only a logging threshold and not passed as `max_tokens=900` in the API call, runaway generation is possible. Locate the `call_claude_agent` invocation for mode=event in `core/llm/` and verify `max_tokens` is hardcoded. Confirm with: `grep -n "max_tokens" bot/core/llm/*.py bot/core/llm/**/*.py`.

2. **Clarify stop=tool_use / tool_called=False** — determine if `tool_called` in `trace_store` tracks only `recommend_entry`/`recommend_exit` or all tools. If it's rec-tools-only, add a comment in `trace_store` to prevent future confusion. If it should track all tools, the trace logger has a bug.

3. **gate_weekly_trend N=1 FNR=1.0** — no tuning yet (N<20), but tag for tracking. When N reaches 20, re-evaluate the weekly-trend gate threshold against `gate_false_negative_rates()`.