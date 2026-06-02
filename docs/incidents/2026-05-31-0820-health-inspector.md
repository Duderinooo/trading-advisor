# Health Report 2026-05-31T08:19:05+02:00 (Sunday)

## Overall: 🟡 degraded
Bug-worker crashes repeatedly with `BlockingIOError [Errno 35]` on `git status` fork; all other subsystems nominal.

## Process hygiene
4 expected PIDs, correct hierarchy. caffeinate wraps both run.sh (PID 1901) and python (PID 1916 `-is -w 1915`). No orphans. Bot restarted at 08:08:30 (shutdown at 08:06:34 logged).

## Pipeline freshness
Heartbeat age: 79474 s (~22 h, last tick 2026-05-30 10:14:31). Expected — today is Sunday, XETRA/US closed, no price-check ticks since Saturday session. `api_calls_today=0` consistent. No anomaly.

## LLM channel
All 4 brain traces present (morning, opening×2, event), zero warnings. agent_runs: last 6 visible entries all `exit_code=0`. No API errors.

## State integrity
`bot.db` + `agent_runs.db` integrity_check both `ok`.

## Gate behavior
`gate_weekly_trend` FNR = **1.0** (1/1 blocked trade would have won). N=1 — below the `MIN_SAMPLE_SIZE_FOR_TUNING=20` threshold, no tuning warranted. Watch accumulation.

`gate_no_entry_zone`: 73 blocks, 0 false negatives — performing correctly.

## Anomalies
**Bug-worker crashing on `git status` fork** — `BlockingIOError: [Errno 35] Resource temporarily unavailable` at 07:55:24 (at least 2 occurrences in log tail). macOS EAGAIN on `fork()` when system file-descriptor or process table is under pressure. This is a recurring failure (multiple incident docs in git status). The crash happens inside `_working_tree_clean()` → `dispatcher.run_agent()` → whole bug-worker cycle aborts. No self-healing retry observed before the 08:06 shutdown.

## Recommended actions
1. **Investigate EAGAIN root cause** — run `ulimit -a` and `sysctl kern.maxfilesperproc kern.maxproc` to check if macOS per-process limits are being hit. The bot spawns caffeinate + git subprocesses; each 15-min cycle may accumulate unclosed FDs.
2. **Add retry with backoff in `bug_worker._git()`** — `BlockingIOError` on fork is transient on macOS; 1–2 retries with 500 ms sleep would recover without crashing the worker. Fix target: `bot/agents/_lib/bug_worker.py:55` (the `subprocess.run` call in `_git()`).
3. **Monitor `gate_weekly_trend` FNR** — currently 1.0 at N=1. If it reaches N=5–10 with FNR > 0.6, consider whether the weekly-trend gate is too strict for current watchlist. No action yet.