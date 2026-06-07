# Health Report 2026-06-03T06:03:54+02:00

## Overall: 🟢 healthy
Clean overnight → morning restart 18s ago; all infrastructure nominal, no open trades, no errors.

## Process hygiene
PID tree clean: run.sh (2805) → caffeinate (2827) + main.py (2831) + `caffeinate -is -w 2831` (2832). All under PGID 2805. No orphans, caffeinate double-wrapped correctly.

## Pipeline freshness
Heartbeat age 27584s (~7.7h). Last tick 22:24:10, bot restarted at 06:03:54 (18s ago per `ELAPSED 00:18`). Gap spans non-market overnight window. No anomaly — bot was live through EOD summary at 22:10, then ran until 22:24; fresh restart visible in log.

## LLM channel
Three event-mode traces exceeded 900-token output budget, none truncated:
- 16:28:36 `out_tok=933/900`
- 17:47:27 `out_tok=1362/900`
- 20:48:24 `out_tok=2044/900`

Pattern is consistent (all `tool_called=False`, haiku, event mode). Not data loss, but budget enforcement appears soft. 11 API calls total yesterday — well within limits.

## State integrity
`bot.db` + `agent_runs.db` both `ok`. All 20 recent agent runs `exit_code=0`, zero errors.

## Gate behavior
`gate_weekly_trend`: 1 block, 1 would-win → FNR=1.0. **N=1 — below MIN_SAMPLE_SIZE=20, no tuning action.** Watch item only.

## Recommended actions
1. **Investigate event-mode token bloat**: `out_tok` hit 2044 vs 900 budget on 2026-06-02T20:48. Check what drove 2044 tokens in `last_event_trace` — likely the geo-news MarketWatch article on S&P 500 speed-run. If event prompts are growing, consider trimming news context passed to haiku. Grep `core/llm/prompt/builder.py` for event-mode context assembly.
2. **Track gate_weekly_trend FNR**: Currently N=1/FNR=1.0. Log the ticker/date so when N reaches 20 you have the history. No tuning yet.