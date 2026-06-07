# Health Report 2026-06-06T12:15:13+02:00

## Overall: 🟡 degraded
Heartbeat 2h stale while process alive on Saturday; `error_max_structured_output_retries` failure in yesterday's event channel.

---

## Pipeline freshness
Heartbeat last tick `2026-06-06 10:14:39` — age **7233s (~2h)** at report time. Process alive (PID 99769). Last log entry `10:00:17` (weekend summary sent). Main loop has produced zero log output in 2h on a Saturday. Could be expected long-sleep-on-weekend behavior, but the heartbeat should still update on each price-check tick. **Unverified whether weekend code explicitly skips heartbeat writes or enters multi-hour sleep.**

---

## LLM channel
`AgentRunError: claude CLI exit=1: error_max_structured_output_retries` visible at top of log tail — session `9a9919b5`, 11 turns, $0.089, `stop_reason=tool_use`. Happened ~16:32 on 2026-06-05 during an event-mode call. Subsequent event calls at 16:48 and 21:16 succeeded (exit_code=0), so channel recovered. Single occurrence, not recurring in the 20 most recent agent_runs entries.

Separate observation: event trace at 21:16 shows `out_tok=4809/900 truncated=False` — output token usage 5× the budget. Not truncated per trace flag, but worth watching if retries recur.

---

## Gate behavior
`gate_weekly_trend`: N=1, FNR=1.0 (1 block, would have won). Below `MIN_SAMPLE_SIZE_FOR_TUNING=20` — no action. Flag for tracking; if it accumulates to 20 blocks at elevated FNR, review the gate.

---

## Anomalies
- `bot.err` is exclusively `MallocStackLogging: can't turn off malloc stack logging` noise from macOS Python subprocesses. Harmless, but ~40 distinct PIDs logged suggests frequent subprocess spawning over many days — expected for agent-CLI calls.
- `api_calls_today=0` on Saturday is expected (no market, no analysis runs).

---

## Recommended actions
1. **Verify heartbeat behavior on weekends**: check `runtime/scheduler.py` or main loop for whether weekend Saturday code path suppresses heartbeat writes or sleeps >15m. If suppressed, add a passive heartbeat write every 15m regardless of market state so staleness detection stays meaningful. Evidence: 2h gap with live PID but zero log output.
2. **Investigate `error_max_structured_output_retries` root cause**: inspect session `9a9919b5-01c3-4a4f-b90f-2e65c86032cc` in `agent_runs.db` (id likely ~330s range from yesterday afternoon) — check which tool schema is causing Haiku to retry 11 times. Add a retry limit or fallback in `agents/_lib/runner.py` before it burns $0.09 per failure.
3. **Monitor `gate_weekly_trend` FNR**: currently N=1/FNR=1.0. No action now, but add a note to revisit once N≥5 for early signal.