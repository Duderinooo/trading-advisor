# Health Report 2026-06-02T14:27:57+02:00

## Overall: 🟡 degraded
Morning prep ERROR — `set_watch_levels NOT called` — plus unresolved LS-TC double timeout on IE00BMTM6B32.

---

## Pipeline freshness
Heartbeat age 36s, market_hours=true. `api_calls_today=6` consistent with visible agent runs (morning + 3 events + 2 openings). Fresh.

## LLM channel
All agent runs exit_code=0. Event-mode tool_called=False across all 4 visible event traces — Claude analyzing but not recommending. Expected given 0 open trades + no morning watch levels set.

## Data quality
`IE00BMTM6B32` LS-TC timeout ×2 consecutive at 13:09:08 and 13:10:34 (read timeout=8.0s). No subsequent success or error in remaining ~75min of log. Unconfirmed recovery.

## State integrity
`bot.db` + `agent_runs.db` integrity_check: ok. `bot.err` is 100% macOS `MallocStackLogging` noise from subprocess spawns — harmless.

## Gate behavior
`gate_weekly_trend` FNR=1.0 (N=1: 1 block, 1 would-win). Below `MIN_SAMPLE_SIZE_FOR_TUNING=20` — informational only, not tuning evidence.

## Anomalies
**Morning ERROR: `TRACE [morning]: set_watch_levels NOT called`** (08:02:47). Morning brief was sent (08:02:49, deterministic render), but Claude emitted no `set_watch_levels` tool call. Consequence: no fresh watch levels established for today. Subsequent event triggers still fire (CBK, 3OIL, 4GLD) — presumably off carry-over levels from prior days — but today's morning scan produced zero actionable watch candidates. No `recommend_entry` in any subsequent event trace.

---

## Recommended actions

1. **Investigate morning set_watch_levels miss.** Check `last_morning_trace` kv_state for full trace detail — did Claude emit any tool call at all, or did it bail early? Run: `sqlite3 state/bot.db "SELECT value FROM kv_state WHERE key='last_morning_trace'"` and inspect `tool_called` + `raw_levels` fields. If raw_levels=0 across all tickers, the watchlist was genuinely empty after liquidity/whole-share filtering; if raw_levels>0 but no tool call, Claude suppressed it (log the text_preview to understand why).

2. **Confirm LS-TC recovery for IE00BMTM6B32.** Check if subsequent price polls succeeded after 13:10. If still failing at next poll cycle: `curl -s --max-time 10 'https://www.ls-tc.de/de/aktie/IE00BMTM6B32'` — if consistently timing out, this ISIN may need to move to an alternate data source or be flagged in `config/` as LS-TC-unreliable.

3. **gate_weekly_trend N=1 FNR=1.0 — log for future review.** Not actionable now (N=1 < 20 threshold), but worth tracking. When N reaches 20, run `gate_false_negative_rates()` again and compare to tuning threshold.