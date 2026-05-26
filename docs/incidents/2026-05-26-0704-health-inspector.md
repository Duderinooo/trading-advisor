# Health Report 2026-05-26T07:03:11+02:00

## Overall: 🔴 critical
Active fd-leak causing `sqlite3.OperationalError: unable to open database file` every 10s in monitoring-agents; prior `OSError: [Errno 24] Too many open files` crashed main.py top-level; DNS failures in last 2min.

---

## Process hygiene

Main process tree intact (run.sh 81221 → caffeinate 81222 → python 81224 → caffeinate 81225, all 8h53m uptime, single PGID 81221).

**Orphan**: PID 98366 `caffeinate -i`, PPID=1, age ~7h50m, outside bot PGID. Not part of bot. Kill manually.

---

## Pipeline freshness

Heartbeat age 31,109s (~8.6h), last tick 22:24:42 yesterday. Pre-market (07:03 CET) so overnight gap is expected. Market opens 09:00 XETRA — morning prep must fire clean. Network must recover first (see below).

---

## LLM channel

`telegram.error.NetworkError: httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known` — DNS resolution failing as of 07:02-07:03, seconds before snapshot. Both Telegram polling and any Claude API calls will fail until network recovers. 10 API calls today already logged — channel was functional earlier.

---

## State integrity

**Active critical failure**: `sqlite3.OperationalError: unable to open database file` in `core/db.py:42 connect()` → `scheduler.py:30 _kv_get()`, firing every 10s at 07:02:41, 07:02:51, 07:03:01. Monitoring-agents subsystem completely non-functional.

Root cause: `OSError: [Errno 24] Too many open files` in bot.err — macOS hit fd limit (default 256). Bot is leaking file descriptors; SQLite `connect()` can no longer open new files. Stacktrace shows crash propagated to `main.py:380 <module>` (top-level), meaning main() raised unhandled. Process still running suggests run.sh restart loop, but all 4 PIDs share identical 8h53m uptime — more likely the `_run_daily_windows` exception was swallowed somewhere and main loop continued in degraded state.

DB integrity checks (bot.db + agent_runs.db) show "ok" — that's a separate PRAGMA call that succeeded, so db files themselves are uncorrupted.

---

## Open-position aging

BAYN.DE entry 2026-05-21 13:35 — **4.7 trading days**. Stale-thesis alert fires at `hold_days_max` per rec. If that threshold is ≤5d for BAYN.DE's setup type, alert fires today. MBG.DE entry date not shown but same cohort. No auto-close risk (alert-only per CLAUDE.md), but review thesis today when market opens.

---

## Recommended actions

1. **Immediately**: check system fd limit — `ulimit -n` in bot's shell; likely 256. Add `ulimit -n 4096` to `run.sh` before the python invocation. This is the root cause of both the "Too many open files" crash and the cascading SQLite failures.

2. **Restart bot** after fixing ulimit — current process is running in degraded state (monitoring-agents dead, SL/TP loop integrity unknown). Verify SL monitoring still functional via log after restart before market opens at 09:00.

3. **Investigate fd leak source**: `lsof -p 81224 | wc -l` to count current open fds. Likely culprit: yfinance HTTP sessions not closed, or SQLite connections in `_lib/scheduler.py` opened without context manager / explicit close.

4. **Kill orphan caffeinate**: `kill 98366` — harmless but unnecessary.

5. **Network**: DNS failure at 07:02 may be transient (sleep/wake DNS glitch). Verify `curl -s https://api.telegram.org` resolves before market open. If persistent, `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`.

6. **BAYN.DE thesis check**: run `/morning` after bot restart to get Sonnet assessment of BAYN.DE position given 4.7d hold. Stale-thesis alert may fire today.

<!-- bug-worker-status: processed ts=2026-05-26 reason=already-fixed-manually -->
