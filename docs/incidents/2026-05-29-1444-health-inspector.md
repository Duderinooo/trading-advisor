# Health Report 2026-05-29T14:41:55+02:00

## Overall: 🟡 degraded
Morning watch-level refresh hard-failed (0 levels returned, existing 2 stale); event output tokens running 2.8× over budget; LS-TC livefeed timeouts recurring since 12:34.

---

## Pipeline freshness
Heartbeat age 32s during market hours — fresh. Process uptime 5h58m, continuous since ~08:43 CET. US opening check (15:35) not yet run — expected.

---

## LLM channel

**Watch-level fail (critical for entry detection):**
```
08:45:37 ERROR core.llm.handlers.persist: MORNING WATCHLEVEL FAIL:
Sonnet returned 0 levels (existing=2). Prompt requires ≥3.
```
Trace preview confirms: `PASS all. No open positions. PUM/DHL/IFX: EXTENDED + D-tier + red_flags...`. Sonnet found nothing worth watching across the entire watchlist — regime-defensiveness or all tickers extended. Watch levels remain at yesterday's stale 2 entries.

**Output token budget overruns:**
| Mode | out_tok | budget | ratio |
|------|---------|--------|-------|
| morning | 4195 | 3500 | 1.2× |
| event (3OIL.MI) | 2531 | 900 | **2.8×** |
| event (4GLD.DE) | 1575 | 900 | 1.8× |

All `truncated=False` — responses complete, but event calls run far above the Haiku budget. This inflates cost and suggests event prompt context is large.

**Morning forced-call race (08:43–08:43:57):**
Auto-run started at 08:43:43, Telegram `/morning` fired at 08:43:45 — 2s gap. Forced call hit 14-min cooldown and deferred. User's `/morning` was silently dropped; auto-run completed the LLM call 91s later. This is per-spec behavior but effectively made the `/morning` recovery lever inert this morning.

**tool_called=False + stop=tool_use pattern (opening + event traces):** Consistent — Haiku is calling a no-op pass tool, not a recommendation tool. Normal defensive PASS behavior, not an error.

---

## Data quality

**LS-TC livefeed timeouts (recurring):**
```
12:34:09 WARN livefeed: LS-TC quote fetch failed IE00BMTM6B32: connect timeout=3.0
13:02:48 WARN livefeed: LS-TC quote fetch failed IE00BMTM6B32: connect timeout=3.0
13:02:54 WARN livefeed: LS-TC quote fetch failed DE000CBK1001: connect timeout=3.0
```
Two tickers affected: IE00BMTM6B32 (ETF, likely 3OIL.MI ISIN) and CBK.DE. Two hits on IE00BMTM6B32 in 28 minutes. Fallback to yfinance should cover, but real-time spread data is unavailable during outages.

**RSS timeouts (13:01, single cycle):**
```
13:01:10 WARN news_rss: Macro RSS fetch failed for topic=WORLD locale=US:en: Read timed out
13:01:24 WARN news_rss: RSS fetch failed for '(PUM OR "PUMA SE") aktie': Read timed out
```
Google News unreachable for ~14s. One news polling cycle with gaps — PUM.DE stock news and US macro missed at 13:01. No follow-up failures visible, appears transient.

**GEO dedup anchor stuck on 08:42 for 3OIL.MI:** 10+ suppressed geo-news entries all referencing the same 08:42 seed. Working correctly — one oil/geo macro event fired, rest deduped. Not an anomaly.

---

## Gate behavior

**gate_weekly_trend FNR=1.0 (N=1):**
```json
{"total": 1, "would_win": 1, "ambiguous": 0, "false_negative_rate": 1.0}
```
Exactly 1 block, would have won. N=1 does not meet `MIN_SAMPLE_SIZE_FOR_TUNING=20` — no tuning warranted. Flag to track: if this gate blocks accumulate with consistent would_win outcomes, review becomes eligible.

**gate_no_entry_zone:** 73 blocks, 0 false negatives — functioning correctly.

---

## Recommended actions

1. **Investigate watch-level fail → consider `/morning` re-trigger.**
   Stale watch levels (2 entries from yesterday) mean entry triggers may be miscalibrated for today's prices. Check `portfolio.json` `.watch_levels` vs current prices. If all tickers are genuinely extended, the fail is correct; if not, run `/morning` manually (user-triggered `bypass_cooldown=True` path) to force a fresh Sonnet pass. Evidence: `08:45:37 ERROR persist: 0 levels returned`.

2. **Audit event prompt context size.**
   Event Haiku calls are burning 2531 tokens vs 900 budget. Check `core/llm/prompt/builder.build_user_message` for the `event` mode — insider data fetch (213–216 filings from tracefour) added in last commit `64bbc94` is likely the culprit. Either truncate insider_data feed to top-N filings before injection, or raise the event `max_tokens` budget consciously. Evidence: `11:15:05 insider_data: fetched 216 filings`.

3. **Monitor LS-TC timeout pattern.**
   IE00BMTM6B32 timed out twice in 28 minutes (12:34, 13:02). If this persists into US session (14:00+ CET), livefeed for oil ETF will be blind to real-time spread data. No action yet — check next cycle log. If a 3rd timeout appears before 15:30, consider temporarily disabling LS-TC for that ISIN and relying on yfinance.

4. **gate_weekly_trend: start tracking sample.**
   Current N=1 with FNR=1.0. Not actionable now, but if you see `total≥5` and FNR stays >0.5, this gate warrants a `gate_false_negative_rates()` deep-dive before tuning eligibility at N=20.