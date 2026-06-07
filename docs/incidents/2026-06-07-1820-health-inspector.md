# Health Report 2026-06-07T18:20:05+02:00

## Overall: 🟢 healthy
Process clean, heartbeat fresh (42s), DBs ok, no errors. Two skill alerts (weekly-calibrator + backlog-keeper) warrant user review but don't degrade pipeline.

## Pipeline freshness
Restart at 10:14:20 today (matches ps elapsed 08:05:47). Clean shutdown+reboot — dedup caches cleared, scheduler running. Post-restart cadence normal. Sunday market-closed: `api_calls_today=1` (the 18:04 Chernobyl geo-news event call) — expected.

## LLM channel
- Event call 18:04→18:06: Haiku, exit_code=0, 94s, `tool_called=False` (PASS verdict on oil geo-news — correct, OPEC+ hike context)
- weekly-calibrator 10:03: sonnet, 158s, `alert=True` — output not surfaced in log tail; user should check Telegram/last output
- backlog-keeper 18:03: sonnet, 195s, `alert=True` — same; content in agent_runs.db `output_size=6679`
- All other agent runs: exit_code=0, no errors

## State integrity
Both DBs pass `PRAGMA integrity_check`. All 4 brain-trace modes (morning, opening_xetra, opening_us, event) clean, zero warnings.

## Open-position aging
1 position: **DBK.DE**, entered 2026-06-03 08:06 — 4 calendar days / 4 trading days. Approaching stale-thesis window depending on `hold_days_max` on that rec. Stale-thesis alert fires once/day if exceeded — verify no alert suppression active.

## Gate behavior
`gate_weekly_trend` FNR=1.0 (1/1 block would have won). N=1 — below `MIN_SAMPLE_SIZE_FOR_TUNING=20`, no action warranted. Flag for future tracking if sample grows.

`gate_no_entry_zone` FNR=0.0 across 77 blocks — correctly filtering auction/EOD windows.

## Recommended actions
1. **Check weekly-calibrator output**: `alert=True` at 10:03 — read the Telegram message or inspect `agent_runs.db` row 359 (`output_size=8906`) for what it flagged (likely Brier haircut shift or gate suggestion).
2. **Check backlog-keeper output**: `alert=True` at 18:03 — row 361, `output_size=6679`. Backlog update may include actionable items.
3. **Monitor DBK.DE thesis freshness**: 4 trading days in; if `hold_days_max` ≤ 5 on that rec, stale-thesis alert fires tomorrow. No action needed today — alert-only, no auto-close.
4. **MallocStackLogging in bot.err**: benign macOS noise from subprocess spawning, no action needed. Consider redirecting stderr of agent subprocesses to `/dev/null` if log noise is unwanted.