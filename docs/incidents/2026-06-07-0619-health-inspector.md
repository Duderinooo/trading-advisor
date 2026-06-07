# Health Report 2026-06-07T06:18 CET

## Overall: 🟡 degraded
Past `error_max_structured_output_retries` failure on 2026-06-05 + event-trace out_tok 5× over budget; current quiescence is expected (Sunday pre-market).

---

## Process hygiene
Clean. PGID 99766: `run.sh` → `caffeinate` wrapper → `main.py` (PID 99769) → `caffeinate -is -w 99769`. Uptime 1d 12h 49m. No orphans, no duplicates.

---

## Pipeline freshness
Heartbeat `2026-06-06 10:14:39` — age 20h, `api_calls_today=0`. Not anomalous: Sunday, XETRA closed. Weekend summary fired 2026-06-06 10:00:17 as expected. No price ticks expected until Monday 08:00 CET morning prep.

---

## LLM channel
**One hard failure** in log tail:
```
AgentRunError: claude CLI exit=1: {"subtype":"error_max_structured_output_retries","num_turns":11,"stop_reason":"tool_use","total_cost_usd":0.08851755}
```
Agent hit 11-turn loop, could not emit valid structured output, burned $0.089, terminated with `stop=tool_use`. Occurred ~2026-06-05 16:32 (log context).

Separate: TRACE `[event]` at 21:16:41 shows `out_tok=4809/900` — 5× over token budget, `truncated=False`. Agent ran, produced output, but blew the budget. No Telegram suppression but over-long response risks context issues.

All agent_runs.db entries since are clean (exit_code=0). System self-recovered.

---

## Gate behavior
`gate_weekly_trend`: N=1, FNR=1.0 — single blocked entry would have won. Below N=20 tuning threshold; no action warranted yet. Monitor accumulation.

`gate_no_entry_zone`: N=77, FNR=0.0. Solid.

---

## Open-position aging
DBK.DE entered 2026-06-03 08:06 — 4 days held. Within normal range; no stale-thesis alert visible in logs.

---

## Recommended actions

1. **Investigate `error_max_structured_output_retries`** (2026-06-05 ~16:32): check `agents/_lib/runner.py` — what triggered 11 turns of tool-call loops without terminal output? Candidate: event mode receiving a news item that required a tool schema Claude couldn't satisfy in allowed retries. Add a guard or reduce tool count for event mode if reproducible.

2. **Investigate `out_tok=4809/900`** (2026-06-05 21:16:41): event mode with GEO news → 4GLD.DE. Token budget `900` is the output cap but response was 5×. Check `core/llm/prompt/builder.py` — if `out_tok` is a soft advisory only, this is fine; if it's a hard budget passed to the API, the model ignored it. Confirm whether `max_tokens=900` is actually sent on event calls.

3. **`gate_weekly_trend` watch**: log this FNR=1.0 data point. When N reaches 20, run `gate_false_negative_rates()` and compare against other gates. No tuning yet.