# Health Report 2026-06-07T12:19:13+02:00

## Overall: 🟡 degraded
Weekly-calibrator fired `alert=True` at 10:03:02 CET; content unknown from this snapshot. All other signals clean.

## Pipeline freshness
Heartbeat age 7493s (~2h 5m). Bot restarted 10:14:20, heartbeat last written at restart. No log activity since startup — Sunday, no market hours, weekend news scan window 18–22 CET. No price-check ticks expected. **Acceptable for Sunday, but monitor at 12:30 for first 15-min tick when news polling resumes.**

## LLM channel
`skill:weekly-calibrator` ran 10:03:02, duration 158s, exit 0 — but `alert=True`. Result: 1266 chars, 6805 output tokens (heavy work). No calibrator output in this snapshot. Root cause unknown.

## Gate behavior
`gate_weekly_trend`: FNR=1.0, N=1 (1 block, 1 would-have-won). N=1 is below the 20-sample tuning threshold — do **not** tune yet. Note for watchlist.

## Recommended actions

1. **Inspect weekly-calibrator output** — retrieve the actual alert message:
   ```bash
   sqlite3 state/agent_runs.db "SELECT output FROM agent_runs WHERE id=359"
   ```
   Determine what triggered `alert=True` before next trading day (Monday 08:00 morning prep).

2. **Monitor first post-restart tick** — confirm heartbeat updates by 12:30 CET (next 15-min poll window). If `age_seconds` still >8100 at 12:35, bot loop may be stalled post-restart.

3. **Track gate_weekly_trend FNR** — currently N=1/FNR=1.0. Flag when N reaches 5+ for early signal; tuning only at N≥20 per policy.