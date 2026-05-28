# Health Report 2026-05-28T19:13:06 CET

## Overall: 🟡 degraded
Rate-limit blackout 17:01–17:43 CET dropped multiple geo-news event analyses (Iran/oil) during active market hours; recovered post-reset but window was missed.

## Process hygiene
**Orphan caffeinate:** PID 98366 (PPID=1, age=2d 20h, PGID 98364) — not part of bot process group 59758. Separate stray, preventing sleep for no reason.

Bot process tree intact: run.sh (59758) → caffeinate -i (59760) + Python main.py (59762) + caffeinate -is -w (59763). All in same PGID.

## Pipeline freshness
Heartbeat age=51s during market hours — fresh. Loop alive.

`api_calls_today=5` is low for a full trading day. Consistent with rate-limit blackout suppressing haiku event calls from ~17:00 until 17:30 reset.

## LLM channel
**Rate-limit blackout 17:01–17:43 CET.** Three consecutive `claude CLI exit=1: "You've hit your limit · resets 5:30pm (Europe/Berlin)"` errors:
- 17:01: bug-watcher agent
- 17:13: event (Iran peace draft news)
- 17:28: event (Irankrieg/US strikes news)

These were high-relevance geo-news for 3OIL.MI and 4GLD.DE. Analysis was **dropped**, not queued. After 17:30 reset, event processing resumed normally (17:43 haiku ok, 18:14 haiku ok).

Recent error rate: 3/5 agent_runs exit_code=1 in the pre-reset window; 0/2 since reset.

## Data quality
`3OIL.MI` dropped by liquidity gate on every news event (spread 2.1–2.6%, threshold 2.0%). Watch-list ticker effectively blocked from analysis on geo-oil events — which are exactly its trigger condition.

`4GLD.DE` dropped by whole-share gate on every event (price €123.42–€123.71 > €100 cap). Gold ticker permanently gated at current capital level.

## Gate behavior
`gate_weekly_trend` FNR=1.0 (N=1, total=1, would_win=1). Single data point — below `MIN_SAMPLE_SIZE_FOR_TUNING=20`, no action warranted. Flag for tracking.

`gate_no_entry_zone` FNR=0.0 across 67 blocks — behaving correctly.

## Anomalies
Event traces show `tool_called=False` on successful haiku runs at 17:44 and 18:14. Both for Iran/oil geo-news. Haiku analyzed but emitted no actionable recommendation. With 3OIL.MI and 4GLD.DE both gated out, the watchlist had nothing tradeable — expected behavior, not a bug, but confirms the liquidity/whole-share gate interaction is eating the entire geo-oil watch segment.

## Recommended actions

1. **Kill orphan caffeinate:** `kill 98366` — stray process, 2d+ uptime, no bot function.

2. **Investigate 3OIL.MI spread gate conflict:** ticker fires geo-news events but is *always* dropped by liquidity gate (spread > 2%). Either remove from geo-news watchlist or accept it will never trade. Check `config/watchlist` and remove 3OIL.MI from news-trigger tickers if it can't pass liquidity gate at current capital.

3. **Investigate 4GLD.DE whole-share price:** gold at €123 > €100 whole-share cap. Either raise `max_affordable_share_price_eur` ceiling (impacts position sizing), switch to a cheaper gold ETC, or remove from geo-news watchlist. Same logic: it fires geo-news but is permanently gated.

4. **Rate-limit blackout — assess missed window:** 17:01–17:43 CET had active Iran escalation news. Two event calls were dropped cold. If this is a recurring near-market-close pattern (Pro/Max limits hit by bot + interactive Claude use combined), consider: (a) reserving haiku quota during market hours by throttling bug-watcher schedule, or (b) logging rate-limit events to a counter so morning-prep sees "N events missed yesterday."

5. **Track gate_weekly_trend FNR:** N=1 today. Check back when N≥5 — if FNR stays high, it becomes a tuning candidate per the research process.