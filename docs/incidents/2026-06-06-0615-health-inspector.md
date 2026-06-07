# Health Report 2026-06-06T06:14:13+02:00

## Overall: 🟢 healthy
Single `error_max_structured_output_retries` from yesterday recovered; overnight silence is market-hours-appropriate; all current processes clean.

## Process hygiene
PIDs 99766–99770 all share PGID 99766, elapsed 12:45:45. Chain: `run.sh → caffeinate -i → main.py → caffeinate -is -w`. No orphans, no duplicate instances.

## Pipeline freshness
Heartbeat `last_tick=2026-06-05 22:24:42` → age ~7.8h. Market closed ~17:30 XETRA; EOD summary ran 22:10; last tick 22:24 is coherent. Pre-market now (06:14), no tick expected. `api_calls_today=7` consistent with overnight + skill runs only.

## LLM channel
One `AgentRunError: claude CLI exit=1: error_max_structured_output_retries` in log tail — 11 turns, $0.088, during `analyze_portfolio`. Occurred pre-16:32 on 2026-06-05; bot restarted 17:28 and ran cleanly through EOD. Not in agent_runs.db recent 20 (all exit_code=0) → error was swallowed before restart.

**Secondary anomaly:** event trace 21:16:41 shows `out_tok=4809/900` (`tool_called=False`, `truncated=False`). Haiku output 5× budget without calling a tool. Result was PASS-equivalent (no Telegram sent), but output-token blowout on a no-action run wastes cost. Worth watching for recurrence.

## State integrity
Both `bot.db` and `agent_runs.db`: `ok`. All four brain traces present with no warnings.

## Open-position aging
DBK.DE entered 2026-06-03 08:06 → 3 days held. Below stale-thesis threshold assuming standard `hold_days_max`. No action needed today.

## Gate behavior
`gate_weekly_trend`: 1 block, FNR=1.0 (blocked trade would have won). N=1 — below `MIN_SAMPLE_SIZE_FOR_TUNING=20`, no tuning warranted. Worth tracking as sample grows.

`gate_no_entry_zone`: 77 blocks, FNR=0.0 — working correctly.

## Recommended actions

1. **Investigate `error_max_structured_output_retries` root cause** — check `runner.py` structured-output retry logic; 11 turns before failure suggests tool-schema validation loop. Consider adding explicit retry cap log before raise so future occurrences have timestamps.

2. **Cap Haiku output tokens on no-tool-call paths** — event at 21:16 burned 4809 tokens for a PASS. If `tool_called=False` is common for geo news, add a short-circuit or reduce `max_tokens` for event mode to match the 900 budget.

3. **Monitor `gate_weekly_trend` FNR** — log current single data point; re-evaluate if FNR stays ≥0.5 at N≥5 before next tuning window.