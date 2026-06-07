# Health Report 2026-06-05T00:09:46+02:00

## Overall: 🟡 degraded
One haiku event call hard-failed (`error_max_structured_output_retries`, 9 turns, $0.11 burned) during Iran/IAEA geo-news at 21:32 — recommendation for 3OIL.MI/3OIS.MI was never delivered.

---

## Pipeline freshness
Heartbeat age 6302s (~1h45m). Market closed, off-hours — acceptable. Last activity: EOD summary at 22:10, bot restarted at 21:58. No cadence gap concern.

---

## LLM channel
`call_claude_agent` exit=1 at 2026-06-04 21:34 (agent_runs.db id=322):
```
subtype: error_max_structured_output_retries
num_turns: 9, stop_reason: tool_use
cost: $0.1098
cache_read_input_tokens: 207,613
```
Model: haiku. Trigger: Iran IAEA geo-news → `analyze_portfolio(mode="event")`. Claude exhausted structured-output retries without emitting a valid tool call. 9 turns = hit the agent SDK retry ceiling. $0.11 on haiku event = abnormally expensive (typical event run ~$0.01–0.03).

Restart at 21:58 was manual/intentional — clean single instance now running.

---

## State integrity
DBs: both `ok`. Brain traces: all 4 present (morning, opening×2, event), zero warnings. No anomalies.

---

## Open-position aging
1 position: DBK.DE, entry 2026-06-03 08:06 (~2 days). Not stale.

---

## Gate behavior
`gate_weekly_trend`: FNR=1.0 — the one blocked trade would have won. **N=1, below tuning threshold (need ≥20).** Flag for monitoring only; no action yet.

`gate_no_entry_zone`: 75 blocks, 0 would-win. Functioning as intended.

---

## Recommended actions

1. **Investigate `error_max_structured_output_retries` root cause** — 9 turns without valid tool call suggests the haiku event prompt + tool schema is triggering a tool-use loop. Check `agents/_lib/runner.py` retry logic and haiku's tool-choice behavior. Reproduce with the cached session ID `9f255c1e-a847-4bb7-af0d-1e3872346a49` if the SDK supports it.

2. **Assess missed Iran/IAEA signal** — 3OIL.MI/3OIS.MI never got a recommendation for the 21:32 news. Manual review: is this a material missed opportunity given current DBK.DE hold?

3. **Watch `gate_weekly_trend`** — FNR=1.0 at N=1. Next time this gate fires, note outcome. Tuning only after N≥20 per policy.

4. **MallocStackLogging in bot.err** — harmless macOS noise but accumulating across many Claude subprocesses. Consider redirecting stderr of Claude CLI calls to /dev/null or a separate log to keep bot.err clean.