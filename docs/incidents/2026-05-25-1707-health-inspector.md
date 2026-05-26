# Health Report 2026-05-25T17:05 CET

## Overall: 🟡 degraded
Current instance stable; morning had 8 restarts + competing-instance conflict, and 15:46 4GLD.DE GEO event completed with zero latency and no Claude call — silent skip suspected.

---

## Process hygiene

Current instance (PID 17236, elapsed 03:50:01) clean:
- `run.sh` → `caffeinate -i` wrapper (PID 17234) ✓
- `caffeinate -is -w 17236` watching Python (PID 17237) ✓
- No orphans

**Historical mess this morning:** 5 distinct startups between 08:44 and 13:15 (08:44 → 09:00 → 09:03 → 09:10 → 09:42 → 11:00 → 11:07 → 13:15). At 09:09, `sibling main.py instances detected: [36841]` — competing instance prevented startup. Telegram Conflict error (`terminated by other getUpdates request`) visible at top of log = two instances briefly fought over bot updates. Resolved by 13:15; stable since.

---

## Pipeline freshness

Heartbeat age 31s at snapshot time, `market_hours=true`. Normal 15-min poll cadence visible in logs. No gaps.

`api_calls_today=9` at 17:05 CET — consistent with: morning prep (possibly 2× due to restarts) + XETRA opening + US opening + 2 event calls + geo-news events. No daily-cap concern.

---

## LLM channel

All `agent_runs.db` records: `exit_code=0`, `error=null`. 0% error rate across 50 recent runs. Model mix correct: haiku for events/opening/bug-watcher, sonnet for health-inspector.

Output token overrun present on every call:
- Event: `out_tok=1904/900`, `out_tok=1905/900`
- Opening: `out_tok=3053/900`, `out_tok=2836/900`

If `/900` is an API `max_tokens` hard cap this would truncate — but `truncated=False` and `stop=tool_use` confirm model stopped naturally. Budget appears soft (prompt hint). Not blocking, but 3× overrun on opening calls is cost leakage.

---

## Anomalies

**4GLD.DE GEO event silent skip at 15:46:59**

```
15:46:59  📰 GEO NEWS → 4GLD.DE: Bond traders surrendering to inflation…
15:46:59  GEO news event done — tool handlers sent any Telegrams
```

Zero elapsed time. No liquidity gate log, no whole-share gate log, no `agent-CLI ok`, no TRACE. Compare to 14:16 3OIL.MI event which shows 34s agent latency + gate logs between those two bookend lines. 4GLD.DE is consistently dropped by the whole-share gate (`price=€126>€100`) but the `📰 GEO NEWS` prefix confirms dedup passed. If all tickers filtered before context build, Claude call should still fire (empty context) and still log. This path exits without a call — code path unclear.

---

## Open-position aging

2 positions: BAYN.DE + MBG.DE, both entered 2026-05-21 13:35 (4 trading days). No stale-thesis alert fired yet. If `hold_days_max` is ≤5 for either rec, alert window opens tomorrow.

---

## Gate behavior

`gate_false_negative_rates()` returns `{}` — no blocked-entry counterfactuals resolved. Expected if recent entries weren't blocked, but means the FNR monitoring loop has no data to surface yet.

---

## Recommended actions

1. **Investigate 4GLD.DE silent skip** — grep `news_monitor` code for early-return paths before the agent call. Specifically: does the handler check `4GLD.DE not in market_data` and return early when all tickers drop? If so, the "GEO news event done" message is misleading — a bond-inflation news item for a gold ETF was silently discarded without Claude seeing it. Check `core/main.py` or wherever `run_news_event` exits.

2. **Root-cause morning restart cascade** — 8 restarts in 4.5h suggests external restarts (user?) rather than crash loops (no stderr, no traceback). But confirm by checking if any crash occurred before 08:44 (log file may have been rotated). If user-initiated, no action needed; if crash-loop, find root.

3. **Verify output token budget semantics** — if `900` in TRACE is the API `max_tokens` param, the model is somehow ignoring it (truncated=False). If it's a soft prompt hint, cost is 3× expected on opening calls. Check `core/llm/` agent invocation to confirm whether `max_tokens=900` is passed to the API.

4. **Monitor BAYN.DE + MBG.DE through tomorrow** — both at 4 days. If `hold_days_max` < 6, stale-thesis alert should fire. Confirm it does; if not, check `_check_stale_theses` scheduling (runs once/day).

<!-- bug-worker-status: processed ts=2026-05-26 reason=already-fixed-manually -->
