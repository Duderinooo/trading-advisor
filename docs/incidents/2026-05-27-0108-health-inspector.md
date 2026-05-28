# Health Report 2026-05-27T01:07:06+02:00

## Overall: 🟡 degraded
Bot crashed with `EMFILE` (fd exhaustion) earlier today and restarted at 19:33 CET; currently stable but root cause unresolved. Claude CLI rate-limit caused monitoring blackout 18:00–19:17 CET. Orphan caffeinate process present.

---

## Process hygiene
PID **98366** (`caffeinate -i`, PPID=1) is orphaned — 25h+ uptime, not in the bot's process group (57574). Predates current bot session by ~20h. No functional harm but consumes a keep-awake assertion unnecessarily.

---

## Pipeline freshness
Heartbeat age 9757s (~2h43m) at report time. Market closed overnight — consistent with last tick 22:24 CET. No anomaly.

Bot **restarted at 19:33:36 CET** (startup banner re-logged). Uptime matches ps elapsed ~5h36m. EOD summary sent at 22:10 — pipeline resumed normally post-crash.

---

## LLM channel
**Rate-limit blackout 18:00–19:17 CET** — Claude Pro limit hit, resets 19:20 CET:
- `skill:bug-watcher` failed at 18:16, 19:17 (exit=1, "You've hit your limit")
- `skill:health-inspector` failed at 19:07 (exit=1, same)
- `skill:bug-watcher` timed out at ~20:01 (244s, exit=-1) — first post-limit run overloaded

Recovery: bug-watcher succeeded at 21:23 (haiku, 92s), eod-postmortem at 22:01 (sonnet, 7.7s). `api_calls_today=7` is low but consistent with rate-limit interruption + overnight.

---

## State integrity
**Root cause of crash**: `OSError: [Errno 24] Too many open files` on `portfolio.json` — `core/portfolio/io.py:33` (`_load_portfolio_raw`). Multiple identical tracebacks in bot.err before restart, all from `run_event_check` → `load_portfolio`. Indicates a file descriptor leak: each load opens `portfolio.json` but something is not closing it before the FD table exhausts (`ulimit -n` typically 256 on macOS).

Bot.db + agent_runs.db integrity: **ok**.

---

## Open-position aging
**MBG.DE** entered 2026-05-21 14:32 → **4 trading days / ~5.5 calendar days** old. Stale-thesis alert fires at `hold_days_max` (rec-specific). No alert logged yet — either within threshold or stale-thesis check hasn't triggered. Monitor tomorrow morning.

---

## Gate behavior
`gate_weekly_trend` FNR=**1.0** (1 block, 1 would-win). N=1 — below `MIN_SAMPLE_SIZE_FOR_TUNING=20`. No tuning warranted; log as data point.

`gate_no_entry_zone` FNR=0.0 across 63 blocks — functioning correctly.

---

## Anomalies
`bot.err` contains repeated `MallocStackLogging: can't turn off malloc stack logging because it was not enabled` from ~12 past Python PIDs. macOS memory-debug artifact from subprocesses (Claude CLI children). Not errors — noise only.

---

## Recommended actions

1. **Fix FD leak in `core/portfolio/io.py:33`** — audit `_load_portfolio_raw`: confirm `open()` is inside a `with` block or has explicit `f.close()`. The crash recurs every run session once FDs exhaust. Check if any caller holds a reference past function return. `grep -n "open(" bot/core/portfolio/io.py` is the starting point.

2. **Kill orphan caffeinate**: `kill 98366`. Verify process group membership first: `ps -o pid,ppid,pgid,comm -p 98366`.

3. **Verify `ulimit -n`** in run.sh context: `ulimit -n` — if it's 256, raise to 4096 in run.sh with `ulimit -n 4096` as a short-term mitigation while the leak is fixed.

4. **MBG.DE stale-thesis check tomorrow morning**: If no `🕒 STALE THESIS` alert fires and position is still open at day 5+, manually review thesis vs current snapshot before market open.

5. **gate_weekly_trend N=1**: No action now — next tuning review when N≥20.