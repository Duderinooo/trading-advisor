# Health Report 2026-06-06T18:16:23 CET

## Overall: 🟡 degraded
One `error_max_structured_output_retries` failure yesterday (exit=1, $0.089 burned, analysis lost). All else nominal for a Saturday.

---

## Process hygiene
Clean. Single process tree: run.sh (PID 99766) → caffeinate (99767) → python main.py (99769) → caffeinate -is (99770). All share PGID 99766, uptime ~24h 48min. No orphans.

---

## Pipeline freshness
Heartbeat age = 28,904s (~8h). Last tick 10:14 CET. Expected — Saturday, market closed. Weekend summary fired at 10:00, heartbeat at 10:14 is last valid tick. `api_calls_today = 0` consistent with weekend. No anomaly.

---

## LLM channel
**Error logged** (appears at start of 80-line tail, predates 2026-06-05 16:32:43):
```
AgentRunError: claude CLI exit=1: {"type":"result","subtype":"error_max_structured_output_retries",
  "num_turns":11,"total_cost_usd":0.08851755,...}
```
11 turns, $0.089 spent, zero usable output — tool-use retry budget exhausted on an event-mode call. System recovered; no subsequent recurrence in log. All agent_runs.db entries after show exit_code=0.

---

## State integrity
Both DBs pass `PRAGMA integrity_check`. All 4 brain trace keys present (morning, opening×2, event), zero warnings.

---

## Open-position aging
1 position: DBK.DE, entry 2026-06-03 08:06 → 3 days held. Not yet stale-thesis territory. Monitor Monday.

---

## Gate behavior
`gate_weekly_trend` FNR = **1.0** (N=1: 1 block, 1 would-have-won). N too small to tune (need ≥20 per tuning rules), but worth watching — if weekly_trend blocks pile up as would-have-won, that gate may be miscalibrated for current XETRA regime.

`gate_no_entry_zone` FNR = 0.0 (N=77) — correct behavior.
`gate_red_team` FNR = 0.0 (N=1, 1 ambiguous) — fine.

---

## Recommended actions
1. **Investigate the structured-output-retry error.** Find the session (`9a9919b5-01c3-4a4f-b90f-2e65c86032cc`) in agent_runs.db — check which ticker/mode triggered it and whether a tool-schema or prompt is causing Claude to loop. `grep -A5 "error_max_structured_output_retries" bot.log` to get the surrounding context. The $0.089 cost with zero output is the worst possible outcome per call.
2. **Watch gate_weekly_trend FNR.** At N=1 no action. If it reaches 5+ would-have-win blocks, pull `gate_false_negative_rates()` and assess whether weekly-trend DOWN criterion needs a RS-override path (similar to the reversal_oversold RSI<30 existing escape hatch).
3. **DBK.DE thesis review Monday morning.** 3-day-old position entering week 2 — morning prep should re-validate thesis before XETRA open.