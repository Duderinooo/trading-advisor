# Health Report 2026-06-05T18:12:13+02:00

## Overall: 🟡 degraded
Recurring `error_max_structured_output_retries` from claude CLI caused US open check (15:36) and one news event (16:18) to fail; plus unexplained mid-session restart at 17:28.

---

## Process hygiene
Clean. PID 99766 run.sh → caffeinate wrapper → Python 99769 → caffeinate `-is -w 99769`. No orphans. PGID consistent.

---

## Pipeline freshness
Heartbeat age 52s at report time — fresh. `api_calls_today=6` is low for a full trading day that included morning prep + XETRA open + US open + multiple news events. Consistent with 2 failed calls not completing.

Bot restarted at 17:28:29 (clean shutdown/startup sequence visible in log). No root cause in log tail — earlier entries cut off. Uptime at report time: 43m45s (matches 17:28 restart).

---

## LLM channel
**3 failures** sharing same error subtype: `error_max_structured_output_retries`

| Time | Mode | Session | Turns | Cost |
|------|------|---------|-------|------|
| ~15:14 | event (pre-log-tail) | 9799a263 | 11 | $0.077 |
| 15:36 | opening/US | fdfcac98 | 9 | $0.087 |
| 16:18 | event (geo news) | 9a9919b5 | 11 | $0.089 |

Pattern: `stop_reason=tool_use`, high turn counts (9-11), all haiku. Claude exhausts retries trying to emit valid tool_use JSON. Next event call at 16:47 succeeded (haiku, 25642ms, exit_code=0) — intermittent, not a hard API outage.

`TRACE [event] 16:48`: `tool_called=False` despite `stop=tool_use`, `out_tok=1804/900` (2× over budget). Over-budget output on successful calls may be related to retry pressure on failed ones — same tool schema, same model.

Brain trace `last_opening_trace_us` shows no warnings but reflects a **stale pre-restart trace**, not the failed 15:36 run. US open was missed this session.

---

## Open-position aging
DBK.DE, entry 2026-06-03 08:06 — age ~58h (2d10h). No `hold_days_max` visible here; stale-thesis alert would have fired if elapsed > rec threshold. No alert seen in log tail → within window or alert already fired earlier today.

---

## Gate behavior
`gate_weekly_trend` FNR=1.0 on N=1: single block that would have won. Below `MIN_SAMPLE_SIZE_FOR_TUNING=20` — no tuning action. **Watch for accumulation.**

`gate_no_entry_zone` 75 blocks, FNR=0.0 — working correctly.

---

## Recommended actions

1. **Investigate `error_max_structured_output_retries`** — check `agents/_lib/runner.py` around the tool-schema loop. The `out_tok=1804/900` overage on successful event calls suggests the tool-response is large; haiku may be hitting a structured-output retry ceiling when output is complex. Likely fix: increase `max_tokens` for haiku event mode, or reduce tool schema complexity. Run: `grep -n "max_tokens\|force_any_tool" bot/core/llm/config*.py` to find current haiku event budget.

2. **US open check was missed today** (15:36 failed, no retry after restart). If DBK.DE or any watch-level needed US-open context, that analysis was skipped. Manual `/morning` or `/analyze` if position requires fresh read.

3. **Find restart root cause** — log entries before 17:28 are cut off in the tail. Run: `grep -B5 "Shutting down" bot/logs/bot.log` to see what triggered the shutdown (Ctrl-C, exception, OOM, or signal).

4. **Monitor `gate_weekly_trend`** — at N=5+ blocked trades, re-evaluate FNR. If it stays elevated, cross-ref with `analytics/setup_expectancy_history.jsonl` for weekly-trend-DOWN entries.