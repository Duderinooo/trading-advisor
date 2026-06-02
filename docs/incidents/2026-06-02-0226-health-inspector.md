# Health Report 2026-06-02T02:25:57+02:00

## Overall: 🟢 healthy
Post-EOD quiet period. All processes nominal, no errors, clean day wrap at 22:10.

## Process hygiene
PIDs 1881/1901/1915/1916 form clean hierarchy: `run.sh → caffeinate → main.py + caffeinate -w`. Both caffeinate instances present. No orphans. PPID 1 for run.sh = systemd/launchd-style root, expected.

## Pipeline freshness
Heartbeat age ~4h01m (last_tick 22:24:36, now 02:26). Market closed. Last activity: EOD summary 22:10, EOD backup 22:10, final news dedup 21:54. 4h silence post-EOD is consistent with overnight idle after `run_eod_summary`. Process still running (uptime 1d18h). No concern, but if no heartbeat by morning prep (~08:00 CET) that's a flag.

## LLM channel
All agent runs exit_code=0. Observed pattern across all event/opening traces: `out_tok` consistently exceeds stated budget (`8881/900`, `2894/900`, `1661/900`, `989/900`) with `truncated=False`. Budget appears to be a soft cost-tracking label, not a hard cap. Not a runtime error, but confirms the `out_tok/budget` field in traces is informational only.

## State integrity
bot.db + agent_runs.db: `ok`. kv_state traces: 4 modes present, zero warnings.

## Gate behavior
`gate_weekly_trend` FNR=1.0 — single blocked trade (N=1) that would have won. Below `MIN_SAMPLE_SIZE_FOR_TUNING=20`. Not actionable yet; worth watching as N grows. `gate_no_entry_zone` (N=73, FNR=0.0) working correctly.

## Anomalies
`eod-postmortem` result_chars=17 (run id 248). Extremely small output — likely "No trades today" variant. Consistent with 0 open trades. Not a bug, but verify postmortem content if you expect substantive output on active days.

`bot.err` contains only `MallocStackLogging: can't turn off malloc stack logging` from subprocess Python invocations. macOS system noise, not application errors.

## Recommended actions
1. **Monitor weekly_trend gate**: when N reaches 20, run `gate_false_negative_rates()` again — current 1/1 FNR could indicate the gate is too strict for current market regime, or be pure noise. Set a reminder.
2. **Confirm morning prep fires cleanly**: heartbeat should refresh by ~08:00 CET morning prep. If stale at next health check, investigate whether loop exited silently post-EOD.