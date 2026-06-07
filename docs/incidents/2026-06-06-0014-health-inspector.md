# Health Report 2026-06-06T00:13 CET

## Overall: 🟡 degraded
One `error_max_structured_output_retries` LLM failure at ~16:32 (recovered); event mode consistently overrunning output token budget 5×.

---

## LLM channel

**`error_max_structured_output_retries`** at ~16:32 UTC+2 in event mode:
```
claude CLI exit=1: subtype=error_max_structured_output_retries
  num_turns=11, cost=$0.089, session=9a9919b5
```
Agent exhausted 11 retries without valid tool use. Recovered — subsequent event calls (16:48, 21:16) succeeded. Intermittent, but $0.089 per failed call is ~10% of daily budget.

**Output token overrun**: both surviving event traces show `out_tok >> budget`:
- id=350: `out_tok=4809/900`, truncated=False
- id=?? earlier: `out_tok=1804/900`, truncated=False

Haiku producing 5× the requested output budget on geo-news events. Not truncating (so tool calls land) but burns tokens and likely causes the structured-output retry loops.

---

## Gate behavior

`gate_weekly_trend`: FNR=1.0 (N=1, total=1, would_win=1). Single blocked trade resolved as winner. Below MIN_SAMPLE_SIZE_FOR_TUNING=20 — no tuning warranted yet, but first data point in the FNR ledger. Worth watching next week.

---

## Pipeline freshness

Heartbeat age: 6515s (~108 min) at report time. Market closed (XETRA opens ~08:00), so gap is expected overnight idle. Last tick 22:24 aligns with EOD summary at 22:10. No anomaly.

---

## Open-position aging

DBK.DE entered 2026-06-03 08:06 — 3 days held. Not yet flagging stale-thesis unless `hold_days_max` < 3. Monitor at open.

---

## Recommended actions

1. **Investigate haiku output overrun**: event calls hitting 4809 tokens vs 900 budget. Check `core/llm/prompt/builder.build_user_message` for mode=event — something in the geo-news context is bloating haiku's response. Likely root cause of `error_max_structured_output_retries` (verbose output → malformed tool-call → retry loop). Look at `agents/_lib/runner.py` — `max_tokens` param passed to haiku for event mode.

2. **Verify `error_max_structured_output_retries` frequency**: query `agent_runs.db` for `error` IS NOT NULL over last 7d: `SELECT ts, mode, error FROM agent_runs WHERE error IS NOT NULL ORDER BY ts DESC LIMIT 20`. If this recurs daily, the output-overrun fix above is urgent.

3. **Watch `gate_weekly_trend` FNR**: currently N=1/FNR=1.0. If it hits N≥5 with persistent high FNR, schedule a tuning review per CLAUDE.md rules.