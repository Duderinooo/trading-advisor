# Health Report 2026-06-04T18:08:35+02:00

## Overall: 🟡 degraded
One `error_max_structured_output_retries` failure (15 turns, $0.16 wasted) in morning window; all post-restart activity clean but root cause unresolved.

## Pipeline freshness
Heartbeat age=22s, market_hours=true. `api_calls_today=5` matches 1 morning + 1 XETRA opening + 1 US opening + 2 price-alert events. Cadence consistent.

Bot restarted at 15:45 (clean shutdown → startup sequence visible). Uptime since restart: ~2h23m. Reason for restart not logged — user action assumed.

## LLM channel
`AgentRunError: claude CLI exit=1: error_max_structured_output_retries` — 15 turns, 110s API time, $0.16 cost, `session_id=2793217d`. Occurred before 09:39. Which agent run failed is unclear (not in the recent-20 agent_runs.db window; must be earlier). All 20 recent runs show `exit_code=0`.

Three event traces today all show `tool_called=False stop=tool_use out_tok≈3500-4300/900`. Haiku analyzed but called no action tool → PASS/HALTEN suppressed at output. Expected behavior under current market conditions.

## Data quality
Liquidity gate consistently dropping BAS.DE (vol_ratio≈0.24-0.26), HEN3.DE (≈0.16-0.19), FRE.DE (≈0.29), CON.DE (≈0.29), RWE.DE (≈0.27), 4GLD.DE (≈0.17-0.23) across all event calls today. These tickers are structurally below `vol_min=0.30` during afternoon hours. 4GLD.DE additionally blocked by whole-share gate (€124.51 > €100 cap). Not a bug — expected for mid-cap XETRA tickers in European afternoon.

Geo-dedup working: 3OIL.MI+3OIS.MI geo story fired once at 08:10, subsequent 8+ hits correctly suppressed for hours.

## State integrity
`bot.db` and `agent_runs.db`: `PRAGMA integrity_check` = ok. All four trace keys present (last_morning_trace, last_opening_trace_xetra, last_opening_trace_us, last_event_trace), no warnings on any.

## Open-position aging
1 position: DBK.DE, entry 2026-06-03 08:06 → age ≈34h. Within normal swing-trade hold window. No stale-thesis concern yet.

## Gate behavior
`gate_weekly_trend`: FNR=1.0 (1 block, 1 would-win). Flags as 100% false-negative rate but **N=1** — below `MIN_SAMPLE_SIZE_FOR_TUNING=20`. No tuning action warranted. Monitor.

`gate_no_entry_zone`: FNR=0.0 across 74 blocks — gate performing as intended.

## Anomalies
`bot.err` contains 40 lines of `MallocStackLogging: can't turn off malloc stack logging` across many Python subprocess PIDs. macOS system noise from agent subprocesses spawned without `MallocStackLogging` env set. Cosmetically noisy, not functionally harmful.

## Recommended actions
1. **Identify the failed agent run**: Query `agent_runs.db` for `exit_code != 0` before run ID ~297 (pre-09:39). Find which agent/mode triggered `error_max_structured_output_retries`. Check if it was a morning-prep call (would mean morning analysis was lost today).
   ```bash
   sqlite3 state/agent_runs.db "SELECT id,ts,agent,mode,error FROM agent_runs WHERE exit_code != 0 ORDER BY ts DESC LIMIT 10;"
   ```
2. **Suppress MallocStackLogging stderr**: Add `MALLOC_STACK_LOGGING=0` or `MallocStackLogging=NO` to the subprocess env in `agents/_lib/runner.py` to clean up `bot.err`.
3. **Track `gate_weekly_trend` FNR**: At N=5+ would-wins, revisit whether the weekly-trend gate is too strict for XETRA mid-caps. No action now.