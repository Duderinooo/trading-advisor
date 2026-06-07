# Health Report 2026-06-05T06:10:37+02:00

## Overall: 🟡 degraded
Haiku hit `error_max_structured_output_retries` on geo-news event call at 21:34 — Iran news cycle dropped. All other signals nominal.

## Pipeline freshness
Heartbeat age 27953s (~7.75h, last tick 22:24:44). Pre-market now (06:10), overnight gap expected. No market-hours activity missed.

## LLM channel
**Structured output failure** at 2026-06-04 21:34:13 — haiku event call, `subtype: error_max_structured_output_retries`, 9 turns, cost $0.11 (10× normal haiku event cost). News check dropped. Trigger: Iran-IAEA geo-news fired 3OIL.MI+3OIS.MI; context likely too large or tool-use schema caused retry spiral.

agent_runs.db row 322: exit_code=1, duration 104s, 7292 output_tokens. No retry after failure.

api_calls_today=5 — consistent with post-restart (~22:00) overnight run, no market hours.

## Gate behavior
`gate_weekly_trend`: N=1, FNR=1.0 — sole block was a would-have-won. Below tuning threshold (N<20), but flag for tracking. If this gate accumulates more blocks, check `gate_false_negative_rates()` again next week.

## Anomalies
Bot restarted at 21:58 — log shows `sibling main.py instances detected: [37463]` → manual restart by user. Current PID 37523 running clean since. The failed event call at 21:34 preceded the restart by ~24 min; unclear if related.

## Recommended actions
1. **Investigate structured-output retry spiral**: check `agents/_lib/runner.py` + haiku event prompt — likely the geo-news context (Iran + oil tickers) with large cache hit (207k cache_read_tokens) caused tool-use loop. Add token-budget guard or reduce geo-news context before haiku call.
2. **Track gate_weekly_trend FNR**: log this N=1 FNR=1.0 observation. When N≥5, re-examine whether weekly-trend gate is miscalibrated for XETRA mid-caps.
3. **Suppress MallocStackLogging noise from bot.err**: set `MALLOC_STACK_LOGGING=0` in run.sh env, or redirect claude subprocess stderr. Currently drowning real errors.