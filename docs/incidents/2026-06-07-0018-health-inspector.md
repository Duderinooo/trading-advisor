# Health Report 2026-06-07T00:17 CET

## Overall: 🟢 healthy
Weekend idle. One recovered LLM retry error (June 5), weekly_trend gate has N=1 FNR=1.0 (watch-only). All systems nominal.

---

## Process hygiene
Clean single-instance tree. PID 99766 (run.sh) → 99767 (caffeinate wrapper) → 99769 (main.py) → 99770 (caffeinate -is -w 99769). All same PGID. Uptime 1d 6h 48m. No orphans.

---

## Pipeline freshness
Heartbeat `last_tick: 2026-06-06 10:14:39` (age ~14h). Expected: heartbeat only advances during market-hours loops; bot ran through EOD (last log: `22:00:13 eod-postmortem`). No gaps in log cadence. `api_calls_today: 0` correct for Sunday pre-dawn.

---

## LLM channel
Single failure June 5 ~16:32: `AgentRunError: claude CLI exit=1: error_max_structured_output_retries` after 11 turns ($0.089 burned). Self-recovered — next event call at 16:48 succeeded. No repeat since. All 20 recent `agent_runs.db` entries exit_code=0.

---

## State integrity
`bot.db`: ok. `agent_runs.db`: ok. Brain traces: all 4 modes (morning, opening_xetra, opening_us, event) present, zero warnings.

---

## Open-position aging
DBK.DE entered 2026-06-03 08:06 — 4 trading days held. Not inherently stale but approaching typical swing-trade window. Stale-thesis check will fire when `hold_days_max` threshold is crossed.

---

## Gate behavior
`gate_weekly_trend`: FNR=1.0 — one block, one would-have-won. **N=1, below MIN_SAMPLE_SIZE_FOR_TUNING=20.** No tuning warranted yet; flag if sample grows.

---

## Recommended actions
1. **Monitor weekly_trend gate**: FNR=1.0 at N=1. Track next blocked entry outcome — if N reaches 5+ with sustained high FNR, write postmortem before any threshold change.
2. **Investigate `error_max_structured_output_retries`** if it recurs: 11 turns before failure suggests prompt or tool-schema issue causing retry loop. One-off is tolerable; second occurrence → inspect `runner.py` retry logic and the tool-schema that triggered it.
3. **DBK.DE thesis review**: Monday morning-prep should include explicit thesis validation — check if original entry thesis (June 3) remains intact after 4-day hold.