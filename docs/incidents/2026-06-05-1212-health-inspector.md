# Health Report 2026-06-05T12:11:19+02:00

## Overall: 🟡 degraded
Morning prep ran but `set_watch_levels` NOT called — watch levels stale for today's session.

## Pipeline freshness
Heartbeat age 23s. api_calls_today=4 at 12:11 CET: morning(1) + event@08:15(1) + opening@09:10(1) + event@11:15(1). Consistent with log. US opening (15:35) not yet due.

## LLM channel
**Previous session (2026-06-04 ~21:50):** `AgentRunError: claude CLI exit=1: error_max_structured_output_retries` — 9 turns, $0.11 cost, session_id `9f255c1e`. Bot shut down and restarted cleanly at 21:58:18. No recurrence today.

Morning token usage: `out_tok=7198/3500 truncated=False` — output 2× over nominal budget but not hard-truncated.

## State integrity
```
TRACE [morning]: set_watch_levels NOT called
```
— logged as ERROR at 08:03:17. Claude produced 7198-token response (`stop=end_turn`) with no tool call. Watch levels were not refreshed this morning. DBK.DE open with stale levels.

## Open-position aging
DBK.DE, entry 2026-06-03 08:06 — **2 days held**. Not stale. Morning trace confirms: "SL 26.0 structural (below MA50 26.32) — hold. TP1 29.0 / TP2 31.48 intact."

## Gate behavior
`gate_weekly_trend`: FNR=1.0 (1/1 blocks would have won). N=1 — below 20-sample tuning threshold (CLAUDE.md rule). No tuning warranted yet; track next block.

`gate_no_entry_zone`: 75 blocks, 0 false negatives — well-calibrated.

## Anomalies
`bot.err` contains only `MallocStackLogging: can't turn off malloc stack logging` noise — macOS system artifact, not actionable.

## Recommended actions
1. **Watch levels stale** — check current watch levels vs DBK.DE price. If DBK.DE has moved significantly since last set_watch_levels call, run `/morning` to force a re-run with `bypass_cooldown=True`. Evidence: `08:03:17 [ERROR] core.llm.telemetry.trace: TRACE [morning]: set_watch_levels NOT called`.
2. **Investigate morning no-tool-call pattern** — `stop=end_turn` + no tool = Claude wrote prose instead of calling `set_watch_levels`. Check `builder.build_user_message` morning prompt for regression (tool schema present? `force_any_tool` set for morning mode?). Previous runs may have same issue — check last 3 morning traces.
3. **Monitor gate_weekly_trend FNR** — currently 1.0 at N=1. No tuning yet, but log which ticker was blocked so next block can be cross-referenced.