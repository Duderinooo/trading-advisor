# Health Report 2026-05-26T15:06:01+02:00

## Overall: 🔴 critical
File descriptor exhaustion caused process crash at ~15:05:30; SL/TP monitoring was blind for ≥1 min with open MBG.DE trade. Bot auto-restarted (31s uptime) but root cause unfixed — will crash again.

## Process hygiene
Orphan caffeinate PID **98366** (PPID=1, PGID=98364, elapsed 15:52:49) predates current run by ~16h. Current run's caffeinate is PID 76339. Orphan holds no wake-lock over the bot process but signals a prior crash without clean teardown.

## Pipeline freshness
Heartbeat age=29s is fresh **only because the process just restarted at 15:05:32**. The previous instance was throwing `[Errno 24] Too many open files` on every poll cycle from at least 15:04:29 until crash at 15:05:30 (~61 seconds of failed polls). Heartbeat writes were also failing (`Heartbeat persist failed`) — so heartbeat data is unreliable as a liveness proxy during the crash window.

## Data quality
`_load_portfolio_raw` could not open `portfolio.json` on every single 10s tick from 15:04:29–15:05:30. Consequence:
- `run_price_check` → `check_stop_loss_take_profit` failed at 15:05:30 (**SL/TP loop was blind**)
- `maybe_auto_kill` failed at 15:05:30
- `run_event_check` failed repeatedly (bot.err: 10+ identical traces)
- `.api_usage.json` reported corrupt on every tick (same FD exhaustion)

## State integrity
Both `bot.db` and `agent_runs.db` pass `PRAGMA integrity_check` — but these were checked by the NEW instance after restart, not during the crash. SQLite was also failing (`sqlite3.OperationalError: unable to open database file`) during the crash window, meaning scheduler KV reads in `_kv_get` (`scheduler.py:30`) were also failing. Any scheduler state writes during that window may be inconsistent.

## Open-position aging
MBG.DE entered 2026-05-21 14:32 — **5 days held**. During the 15:04:29–15:05:30 crash window, SL was not being monitored. Price at crash-time unknown. Verify SL is still valid and position hasn't moved through SL level undetected.

## Anomalies
- **FD leak root cause unresolved.** `core/portfolio/io.py:_load_portfolio_raw` (line 33) and `core/db.py:connect` (line 42) are both leaking file descriptors somewhere upstream. The process hit the OS FD limit (`RLIMIT_NOFILE`, typically 256 on macOS default). Will reproduce on next long run.
- **Bot in crash loop before restart.** `main.py:380` is `in <module>` — the top-level `main()` call raised unhandled. `run.sh` restarted it. The loop was: crash → restart → accumulate FDs → crash again. The current 31s-old instance may crash again once FD count climbs.
- **bug-watcher IDs 99–100**: `result_chars=24` (nearly empty output) at 15:04:29–15:05:09 — these ran during the crash window and likely got degraded input. Not an agent error per se, but confirms monitoring was compromised.

## Recommended actions

1. **Immediate — check MBG.DE SL**: Run `lsof -p 76338 | wc -l` to see current FD count, then manually verify MBG.DE hasn't moved through its SL during the ~1-min blind window. If uncertain, check TR app directly.

2. **Fix FD leak — high priority**: Audit `core/portfolio/io.py:_load_portfolio_raw` — confirm `open()` uses a `with` context manager. Check `core/db.py:connect` — if it returns a raw connection without ensuring `.close()` on every path, wrap in context manager or connection pool. Run `lsof -p 76338 | grep -c portfolio.json` to confirm leak is accumulating in the new instance.

3. **Kill orphan caffeinate**: `kill 98366`

4. **Raise FD limit as interim mitigation**: Add `ulimit -n 4096` to `run.sh` before the `python main.py` call. Buys time but does not fix the leak.

5. **Investigate scheduler KV inconsistency**: After fixing the leak, check `state/bot.db` kv_state table for agent scheduler entries (`SELECT * FROM kv_state WHERE namespace='scheduler'`) — writes during crash window may have partial/wrong timestamps, causing agents to skip or double-run on next cycle.

<!-- bug-worker-status: processed ts=2026-05-26 reason=already-fixed-manually -->
