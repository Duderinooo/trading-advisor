# Health Report 2026-05-28T01:10:34+02:00

## Overall: 🟡 degraded
Claude CLI rate limit caused 2 missed event analyses for 3OIL.MI (20:34–20:50 CET); recovered by 21:07. One orphan caffeinate process unrelated to current session. Everything else nominal.

---

## Process hygiene
PID 98366 (`caffeinate -i`, PPID=1) running 2d 1h 57m — orphan, not part of current bot session (current session is PGID 59758). Harmless but stale.

Current bot stack intact:
- 59758 `run.sh` (PPID=1) → 59760 `caffeinate -i` wrapper → 59762 `main.py` → 59763 `caffeinate -is -w 59762`

All 4 PIDs share PGID 59758 — no duplicate instances.

---

## Pipeline freshness
Heartbeat age 9963s (~2h46m). Last tick 22:24:31 CET; now 01:10 CET. Market closed; process confirmed alive (PID 59762 up 6h 52m). Gap consistent with post-EOD off-hours loop sleep. Not alarming, but worth cross-checking if still stale at next market-open check.

`api_calls_today=8` — suppressed by rate-limit outage earlier, not a model-budget issue.

---

## LLM channel
Rate limit hit: **3 consecutive CLI exits=1** (`"You've hit your limit · resets 9pm (Europe/Berlin)"`) between 19:10–20:05 CET:
- `skill:health-inspector` failed ~19:10
- `skill:bug-watcher` failed ~20:05
- `call_claude_agent mode=event` failed at 20:34 and 20:50 (2 Iran-news events for 3OIL.MI → no Claude analysis)

Recovered after 21:07 reset: bug-watcher ✓ (121s), event haiku ✓ (45s), eod-postmortem ✓ (104s).

Anomaly: event call at 21:21 logged `out_tok=1182/900` — exceeded token budget but `truncated=False`. Output overflow without truncation flag; the tool still fired (`n_actions=1`). Verify `max_tokens` enforcement in `runner.py` handles this correctly.

---

## Data quality
3OIL.MI consistently dropped by liquidity gate (spread 2.76–3.36%) across all three event cycles (20:34, 20:50, 21:19). Ticker present on watchlist but structurally illiquid at those times — expected behavior, not a bug. Two of those cycles failed entirely due to rate limit anyway.

4GLD.DE dropped by whole-share gate (€123.19–20 > €100 cap) — expected.

---

## State integrity
Both DBs: `PRAGMA integrity_check = ok`.

All 4 brain traces present with no warnings (morning, opening_xetra, opening_us, event).

eod-postmortem `meta_json: {result_chars:17, input_tokens:3, output_tokens:10}` — near-zero output. With 0 open trades and no closed trades today, this is correct (nothing to postmortem).

---

## Gate behavior
`gate_weekly_trend`: FNR=1.0 (1/1 blocked trade would have won). **N=1 — tuning not actionable per MIN_SAMPLE_SIZE_FOR_TUNING=20.** Monitor for accumulation.

`gate_no_entry_zone`: 67 blocks, FNR=0.0 — performing correctly.

7 blocked entries still pending outcome resolution (logged at 22:10).

---

## Anomalies
EOD outcomes: 4 resolved, 0 would-have-won. Combined with FNR=0.0 on entry-zone gate — no missed winners today. Flat day.

---

## Recommended actions

1. **Orphan PID 98366**: `kill 98366` — stale caffeinate, not protecting anything.

2. **Rate-limit gap**: Two 3OIL.MI Iran-deal news events (20:34, 20:50) reached no Claude. Check if the position thesis for any 3OIL.MI watch level needs manual review given Iran-deal progress during that window.

3. **Token overflow (event 21:21)**: `out_tok=1182` vs budget `900` with `truncated=False`. Inspect `bot/agents/_lib/runner.py` — if `max_tokens` is passed to the CLI but not enforced on the response side, add a post-call check or raise budget to 1300 for event mode.

4. **gate_weekly_trend N=1 FNR=1.0**: Log the ticker/date for that block. At N=10 revisit — if FNR stays elevated, bring to tuning committee with `gate_false_negative_rates()` + `compute_hit_stats()`.