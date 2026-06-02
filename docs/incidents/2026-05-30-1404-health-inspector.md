# Health Report 2026-05-30T14:03:48+02:00

## Overall: 🟡 degraded
Heartbeat stale 3h49m with process confirmed alive — loop may be idle. Telegram DNS failure in recent log, though weekend summary confirmed sent.

## Process hygiene
Clean tree: `run.sh` (45982) → `caffeinate -i` (45983) → `main.py` (45985) → `caffeinate -is -w` (45985). No orphans. Caffeinate present on both wrapper and Python watcher.

## Pipeline freshness
Last heartbeat tick: `10:14:31 CET` — **age 3h49m** at report time. Process is alive (confirmed via `ps`). On a Saturday the main loop should still tick every ~15 min even if all market-hours tasks are skipped. A 3h49m gap indicates either the loop is not iterating or heartbeat write is broken. `api_calls_today=0` consistent with weekend, but does not explain heartbeat silence.

## LLM channel
All agent_runs clean: exit_code=0 for all 20 recent entries. No LLM errors. Correct model routing observed (sonnet for health-inspector/morning, haiku for event calls). Runs nominal.

## State integrity
`bot.db` + `agent_runs.db` both `PRAGMA integrity_check = ok`. All 4 brain traces (`morning`, `opening×2`, `event`) present with zero warnings.

## Gate behavior
`gate_weekly_trend`: **FNR = 1.0** (1 blocked, 1 would have won). N=1 — below tuning threshold of 20, no action warranted. Flag for tracking: if this accumulates, weekly-trend gate may be too strict on down-trend entries.

`gate_no_entry_zone`: 73 blocks, 0 false negatives. Healthy.

## Anomalies
**Telegram DNS failure** visible in log tail: `httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known`. Timestamp not visible in the 80-line tail, but weekend summary sent `10:00:13` → connectivity recovered. Likely transient upstream DNS blip.

`bot.err` contains only macOS `MallocStackLogging` noise — harmless, not actionable.

## Recommended actions
1. **Investigate heartbeat staleness**: Check if main loop is actually iterating. Run `grep "heartbeat\|tick\|loop" /Users/malteollmann/Private\ Repos/trading-advisor/state/bot.log | tail -20` to find last tick log entry. If last heartbeat write was ~10:14, determine what caused the loop to stall for 3h+ on a Saturday.
2. **Monitor `gate_weekly_trend` FNR**: Currently N=1, not actionable. At N≥5 reassess whether a reversal override (RSI<30 + Selling-Exhaustion) should be broadened.
3. **Telegram DNS error**: No immediate action — connectivity self-healed. If it recurs on a trading day (network loss during market hours = missed alerts), consider adding DNS retry logging or a local DNS fallback.