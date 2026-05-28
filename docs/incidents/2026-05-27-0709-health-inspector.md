# Health Report 2026-05-27T07:08 CET

## Overall: 🟡 degraded
File-descriptor leak crashed previous bot instance yesterday ~19:33; current instance healthy since restart but same leak will recur. Claude CLI rate-limit blacked out monitoring agents for ~1h yesterday afternoon.

---

## Process hygiene

Clean active tree: `run.sh(57574) → caffeinate -i(57576) + python main.py(57579) + caffeinate -is -w(57580)`. All in same PGID 57574. Uptime 11h37m since 19:33 restart.

**Orphan:** PID 98366 `caffeinate -i` with PPID=1, elapsed 1d 7h55m — no parent, predates current bot instance. Leftover from a prior session. Not harmful but wasting a system wake-lock.

---

## Pipeline freshness

Heartbeat age 31423s (~8.7h). Last tick 22:24:29 — this is post-EOD, pre-market now (07:08). Overnight gap expected; no anomaly.

`api_calls_today=7` consistent with pre-market, no LLM calls yet today.

---

## LLM channel

**Rate-limit blackout 18:16–19:17 yesterday (CET):**
- `skill:bug-watcher` failed at 18:16, 19:17 — "You've hit your limit · resets 7:20pm"
- `skill:health-inspector` failed at 19:07 — same limit
- 3 agent runs lost; Telegram alerts suppressed by dedup

Post-reset recovery: bug-watcher timed out once (244s, id=115) then succeeded at 21:23 (92s). EOD postmortem and health-inspector both ok after reset.

**Missed health alert:** health-inspector ran at 01:08, raised alert, but `send_alert suppressed (dedup): 🤖 Monitoring-Agent` — user received no Telegram for that run.

---

## Data quality

**`OSError: [Errno 24] Too many open files`** — repeated in bot.err, all at `core/portfolio/io.py:33 _load_portfolio_raw`. Triggered during `_run_poll_cycle → run_event_check → load_portfolio`. Multiple consecutive occurrences → bot crashed and restarted at 19:33:36 (startup banner visible in bot.log).

Root cause: fd leak building over time. `_load_portfolio_raw` opens `portfolio.json` but something upstream is not closing fds. The fix landed for SQLite (`fd97ad fix(db): close sqlite connections on context exit`) but portfolio.json itself still leaks.

**MallocStackLogging noise** in bot.err: 23 lines `Python(pid) MallocStackLogging: can't turn off...` — benign, from claude CLI subprocesses. Not actionable.

---

## Open-position aging

1 open trade: **MBG.DE**, entry 2026-05-21 14:32 — **6 days old**. Stale-thesis alert fires at `hold_days_max` (rec-specific). If that threshold is ≤6d, user has or will receive a 🕒 STALE THESIS alert today.

---

## Gate behavior

`gate_weekly_trend`: FNR=1.0 (1 block, 1 would-win). N=1, far below MIN_SAMPLE_SIZE_FOR_TUNING=20. **Not a tuning signal** — flag only for awareness. Monitor as more blocks accumulate.

`gate_no_entry_zone`: FNR=0.0 across 63 blocks. Functioning correctly.

---

## Anomalies

Previous process ran long enough to exhaust fds then crash. The fact that it crashed mid-cycle (event_monitor) rather than at a safe point means any in-flight event analysis at ~19:33 was lost without dedup protection (dedup entry may or may not have been written before crash). Worth checking if any watch-level hits were silently dropped during the crash window (~18:16–19:33).

---

## Recommended actions

1. **Fix fd leak in `core/portfolio/io.py:_load_portfolio_raw`** — ensure `open()` is always inside a `with` block. Check every caller that passes file handles. Same pattern that was fixed for SQLite (fd97ad). This is the highest-priority fix; current bot will crash again under sustained load.

2. **Kill orphan PID 98366**: `kill 98366` — stale `caffeinate -i` holding system wake-lock for 32h with no parent.

3. **Check dedup TTL for `🤖 Monitoring-Agent` alert** — health-inspector at 01:08 fired alert but was suppressed. If the dedup window covers the full inter-run gap, monitoring alerts are silently swallowed. Verify dedup key lifetime vs. health-inspector cadence (4× daily = 6h apart).

4. **Review MBG.DE thesis freshness** — 6 days held, XETRA open in ~2h. If no stale-thesis alert has fired yet, verify `hold_days_max` on that rec. If thesis has decayed, surface to `/morning` for Claude review.