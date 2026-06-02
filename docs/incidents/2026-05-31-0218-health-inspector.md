# Health Report 2026-05-31T02:18:09+02:00

## Overall: 🟢 healthy
Overnight quiet period. Process tree clean, DBs ok, no open positions. One transient Telegram DNS error; self-healing retry loop active.

## Process hygiene
PID 75312 (run.sh) → 75313 (caffeinate -i wrapper) → 75315 (Python main.py) → 75316 (caffeinate -is -w 75315). All same PGID, correct parent chain, uptime ~11h47m. No orphans, no duplicates.

## Pipeline freshness
Heartbeat age 57,818s (~16h). Last tick: 2026-05-30 10:14:31 CET (mid-XETRA session yesterday). Current time 02:18 CET, `market_hours: false`. Gap is expected — bot ticks during market hours only. `api_calls_today: 0` consistent with overnight. No anomaly.

## LLM channel
All 4 brain traces (morning, opening×2, event) clean — no warnings. agent_runs: 0 errors, exit_code 0 on all recent runs. One outlier: health-inspector run id=213 took **409s** vs 50–97s for prior two runs. Likely slow API response or network congestion during that window. Didn't fail.

## State integrity
`bot.db` and `agent_runs.db`: `PRAGMA integrity_check = ok`.

## Gate behavior
`gate_weekly_trend`: N=1, FNR=1.0 — one blocked entry would have won. **Too small to act on** (tuning rule: N≥20). Track. `gate_no_entry_zone`: 73 blocks, FNR=0.0 — clean. `gate_red_team`: 1 block, 1 ambiguous, FNR=0.0 — fine.

## Anomalies
**Telegram DNS failure** in bot.log: `telegram.error.NetworkError: httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known`. Transient network outage hit the polling loop. `network_retry_loop` auto-retries — bot self-heals. Agents ran successfully after (20:18, 22:00), confirming connectivity restored. User commands and alerts functional again.

**eod-postmortem result_chars=17** (both id=210 and id=214). Consistent across two runs — likely intentional minimal output ("No trades today." pattern). Not a bug, but worth confirming once that output is expected when no trades closed.

**bot.err**: All `MallocStackLogging: can't turn off malloc stack logging` — macOS/Homebrew Python 3.14 noise. Not actionable.

## Recommended actions
1. **gate_weekly_trend FNR**: Now at N=1, would_win=1. Start tracking; if it reaches N≥20 with FNR>0.3, run `gate_false_negative_rates()` and open a tuning postmortem per the tuning rules.
2. **health-inspector 409s run**: Check if this correlates with Claude API latency or the Telegram network outage window around the same time. If 400s+ runs become frequent, consider adding a timeout/alert in the dispatcher.
3. **eod-postmortem**: Confirm `result_chars=17` is the expected "no trades" output format. If it should produce more, investigate the postmortem skill prompt.