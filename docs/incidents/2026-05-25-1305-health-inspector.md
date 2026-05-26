# Health Report 2026-05-25T13:04:15+02:00

## Overall: 🟡 degraded
Opening Haiku burned 3053 output tokens against a 900-token budget (3.4×) while emitting zero new watch levels. Additionally, 6 restarts in under 3 hours this morning including one blocked multi-instance collision.

---

## Process hygiene

Single clean process tree — all four PIDs share PGID 79740, PPID chain intact, caffeinate wrapping both shell and Python PID. No orphans.

However: **6 restarts logged 08:44–11:07** (bot.log: repeated startup banners). At 09:09:37, multi-instance guard fired:
```
sibling main.py instances detected: [36841] — refusing to start
```
Guard worked correctly, but the collision indicates a manual restart raced a live instance. Current instance stable since ~11:07 (uptime 01:57).

---

## Pipeline freshness

Heartbeat age=1s at snapshot time, market_hours=true. Price-check cadence appears nominal — GEO dedup entries every 15–30 min confirm polling is running. No gaps detected.

---

## LLM channel

Opening trace (09:06:08):
```
out_tok=3053/900  tool_called=False  raw_levels=0  stop=tool_use
```
3053 output tokens against 900-token configured budget — 3.4× overage. `raw_levels=0` means no actionable output despite the token spend. This is wasteful: haiku producing ~3k tokens of reasoning that results in nothing emitted. Confirmed in agent_runs ID 72: `output_tokens: 3053`.

`tool_called=False` with `stop=tool_use` is contradictory — suggests trace field semantics may be off (tool_called=False tracks trade-action tools, stop=tool_use means model did call *some* tool). Not necessarily a bug but worth verifying.

api_calls_today=6 is low and consistent with the run history.

---

## Data quality

Liquidity gate at opening (09:05:21) dropped 5 tickers:
- `3OIL.MI` — spread 2.183% (above cap)
- `AIXA.DE`, `DBK.DE`, `IFX.DE` — vol_ratio 0.01–0.02 (below floor)
- `4GLD.DE` — price €126.08 > €100 whole-share cap

That's 5/10 monitored instruments unavailable at XETRA open. Early-morning XETRA illiquidity is expected for some, but IFX.DE and DBK.DE are large-caps — vol_ratio 0.01 at 09:05 may be data-feed latency, not genuine illiquidity (yfinance at open is notoriously slow to populate intraday volume). Worth checking whether the opening check is scheduled too early for yfinance volume data.

---

## Open-position aging

2 positions open, oldest entry 2026-05-21 13:35 — **4 calendar days** (3 trading days: Mon–Thu). Both BAYN.DE and MBG.DE. No stale-thesis alert fired yet (bot sends once `hold_days_max` exceeded). Monitor tomorrow if no exit rec.

---

## Anomalies

**Telegram Conflict errors pre-08:44**: Two `telegram.error.Conflict: terminated by other getUpdates request` errors logged before the 08:44 restart. Indicates an older instance was still polling when a new one started. The conflict self-resolved via restart, but this is a symptom of the restart churn pattern above — manual restarts without clean shutdown of the previous instance's Telegram polling.

**No morning agent run in agent_runs.db visible in last 50 rows**: morning_trace exists in brain traces (no warnings), but no `call_claude_agent mode=morning` entry in the last 50 rows. Morning ran in a pre-log-window session (before 08:44 first visible entry) — this is plausible since morning prep runs at 08:00. Not a gap per se, but cross-referencing: brain trace exists, so morning did complete.

---

## Recommended actions

1. **Investigate opening token overage**: `out_tok=3053/900` with zero output — check `core/llm/prompt/builder.py` for what fills the opening prompt context and why Haiku generates 3k tokens to conclude nothing. May need to tighten the system prompt or add a max_tokens hard cap on the CLI call.

2. **Check XETRA opening vol_ratio timing**: IFX.DE and DBK.DE at vol_ratio=0.01 at 09:05 is suspicious for large-caps. Push `run_opening_check` start to 09:10 or add a vol_ratio fallback that uses previous-day volume when intraday volume < 5-min threshold. A missed opening signal on DBK/IFX is a real cost.

3. **Audit restart-trigger this morning**: 6 restarts in 2.5h is high. Check if a code change or config change drove them. If manual debugging, no action. If a crash loop caused it, identify the root trigger — the log window starts mid-churn so the initial cause is not visible.

4. **Verify `tool_called` trace semantics**: `stop=tool_use` + `tool_called=False` in the opening trace — confirm whether `tool_called` in `core/llm/telemetry/trace_store` means "trade-action tool called" or "any tool called". If the former, the field name is misleading; rename or add `trade_tool_called` to disambiguate.

<!-- bug-worker-status: processed ts=2026-05-26 reason=already-fixed-manually -->
