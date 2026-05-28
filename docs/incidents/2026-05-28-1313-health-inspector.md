# Health Report 2026-05-28T13:12:08+02:00

## Overall: 🟡 degraded
Rate-limit wiped XETRA morning window (~09:00–12:30 CET); opening checks + news events + bug-watcher all failed. Coverage restored 12:57. No open positions at risk, but 3.5h of market blindness during prime XETRA session.

---

## Process hygiene
Orphan `caffeinate -i` (PID 98366, PPID=1, PGID=98364) running 2d13h — not in the bot's PGID (59758). Not part of current session. Likely leftover from a prior run.sh restart. Benign but should be killed if confirmed stale.

---

## Pipeline freshness
Heartbeat age=29s ✓ — bot ticking normally *now*. But `api_calls_today=3` at 13:12 with market open since 09:00 signals the morning was effectively dark. Expected calls for a normal XETRA day by 13:12: morning (1) + 2× opening (2) + ~10–15 event/standard cycles = 13+. Only 3 made it through.

---

## LLM channel
Rate-limit errors (`"You've hit your limit · resets 12:30pm"`) hit every call from ~09:47 until reset:

| Time | Call | Result |
|------|------|--------|
| ~09:47 | opening_check XETRA | ❌ rate-limited |
| 10:00 | bug-watcher | ❌ rate-limited |
| 11:00 | bug-watcher | ❌ rate-limited |
| 12:00 | bug-watcher | ❌ rate-limited |
| 12:26 | news event (4GLD.DE) | ❌ rate-limited |
| 12:57 | news event (4GLD.DE) | ✅ haiku, 46s, actions=1 |
| 13:01 | bug-watcher | ✅ haiku, 30s |

Post-reset calls succeeded. No API errors in current window.

---

## Gate behavior
`gate_weekly_trend` FNR = **1.0** (1 block, 1 would-have-won). N=1 — below `MIN_SAMPLE_SIZE_FOR_TUNING=20`, no action warranted. Flag for tracking: if weekly_trend FNR accumulates to ≥20 over next weeks, investigate whether DOWN-trend gate is miscalibrated for current XETRA conditions.

---

## Anomalies
`bot.err` contains ~40 `MallocStackLogging: can't turn off` lines. Benign macOS debug artifact from Python subprocess spawns — not an error. Consider suppressing in the err-tail collector (`grep -v MallocStackLogging`) to reduce noise in future reports.

`4GLD.DE` dropped by whole-share gate (price €121.79 > €100 cap) on both 12:26 and 12:57 cycles. Consistent behavior — not a bug — but means gold ETC news triggers a Claude call that then drops the ticker. Low-cost waste.

---

## Recommended actions

1. **Kill orphan caffeinate**: `kill 98366` — confirm it's not intentional first (`lsof -p 98366`).
2. **Investigate rate-limit root cause**: 3 successful calls by 13:12 suggests the daily limit was hit *before* market open, likely from yesterday-evening agents or a manual session. Check Claude Code usage dashboard for 2026-05-28 pre-09:00 consumption.
3. **Add rate-limit guard in runner.py**: `runner.py:207` raises `AgentRunError` on any CLI exit=1. Rate-limit is a known recoverable error — parse the `"You've hit your limit"` string and raise a distinct `RateLimitError` so the dispatcher can suppress retries until the reset time rather than retrying on the next cron tick.
4. **Suppress MallocStackLogging in err-tail**: Add `| grep -v MallocStackLogging` to the health-inspector's err collection command to keep signal-to-noise ratio useful.
5. **4GLD.DE pre-filter**: If user doesn't intend to trade this ETC (price > cap), add it to a skip-list before the news-trigger fires a Claude call, saving ~1 Haiku call per news cycle.