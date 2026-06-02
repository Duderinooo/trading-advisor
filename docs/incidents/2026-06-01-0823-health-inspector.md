# Health Report 2026-06-01T08:22:44 CET

## Overall: 🟡 degraded
Morning Sonnet verbally recommended dropping CBK from watch but did **not** call `set_watch_levels` — stale watch level persists. One gate_weekly_trend block was a false negative (FNR=1.0, N=1).

---

## Pipeline freshness
Heartbeat age 21s at report time, market_hours=true, cadence intact. Morning prep ran 08:00:09, event call 08:09:43 on 3OIL.MI -7.4% price alert. All traces present (morning + both opening + event), no warnings.

---

## LLM channel
Morning Sonnet: `out_tok=4416/3500` — over token budget but `truncated=False`. Not a one-off (previous runs also near/over). Model mix correct: sonnet for morning/health, haiku for event. All agent_runs exit_code=0, no API errors today.

---

## State integrity
`set_watch_levels NOT called` (ERROR, 08:02:21). Morning trace confirms `tool_called=False`. Sonnet explicitly said "CBK stale — price €37.10 above old zone, quality D, drop watch" but emitted no tool call to enact it. CBK.DE watch level remains in portfolio.json.

---

## Gate behavior
`gate_weekly_trend`: FNR=1.0 (1/1 block would have won). N=1 — below MIN_SAMPLE_SIZE_FOR_TUNING=20, no tuning warranted. Monitor accumulation. `gate_no_entry_zone`: 73 blocks, FNR=0.0 — clean.

---

## Anomalies
- DNS failures 2026-05-31 ~19:04: Telegram `NetworkError: httpx.ConnectError: [Errno 8]` + LS-TC resolution failure for IE00BMTM6B32, DE000CBK1001. Recovered by next cycle, not ongoing. macOS DNS hiccup.
- bot.err: 40 lines all `MallocStackLogging: can't turn off...` — macOS system noise, no action needed.
- Morning output tokens consistently over budget (4416 vs 3500 cap). Prompt context growing?

---

## Recommended actions

1. **[immediate]** Drop CBK.DE from watch manually or trigger `/morning force` to get a fresh Sonnet call that may issue the `set_watch_levels` tool. Root cause: morning ended `end_turn` without tool use despite a stated recommendation — investigate `core/llm/prompt/` whether `set_watch_levels` schema is visible in the morning tool list and if Sonnet is being instructed to call it for drops (not just additions).

2. **[investigate]** Morning token budget: `out_tok=4416` vs 3500 limit. Check `core/llm/prompt/builder.build_user_message` for recently added context that isn't cache-stable or is growing (insider data now 208 filings from tracefour since commit `275b0e6`).

3. **[watch]** `gate_weekly_trend` FNR=1.0 at N=1. Log the ticker/date so next tuning review has the data point. No threshold change until N≥20.