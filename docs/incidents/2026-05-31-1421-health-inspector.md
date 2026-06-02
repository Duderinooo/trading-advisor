# Health Report 2026-05-31T14:20:12+02:00

## Overall: 🟢 healthy
Weekend mode. Processes nominal, DBs clean, morning tasks completed, transient Telegram DNS blip self-resolved.

## Process hygiene
Clean hierarchy: run.sh (1881) → caffeinate (1901) → main.py (1915) → caffeinate -is -w 1915 (1916). All same PGID 1881. No orphans. Proper caffeinate wrapping.

## Pipeline freshness
Heartbeat age 14742s (~4.1h, last tick 10:14:30). Saturday, market_hours=false — expected. Morning tasks done: summary at 10:00:07, weekly-calibrator at 10:00:57. Loop idle since. `api_calls_today=0` correct for non-trading day.

## LLM channel
weekly-calibrator: sonnet, 49s, exit_code=0, 527 result_chars. All 20 recent agent_runs exit_code=0, no errors.

## State integrity
bot.db: ok. agent_runs.db: ok. All 4 brain traces present (morning, opening_xetra, opening_us, event), zero warnings each.

## Gate behavior
- `gate_weekly_trend` FNR=1.0 — **N=1, not tuning-eligible** (<20 samples per tuning rules). Flag for future tracking; revisit at N≥20.
- `gate_no_entry_zone` FNR=0.0 across 73 blocks — correct calibration.
- `gate_red_team` FNR=0.0 (1 ambiguous, 0 would-win) — fine.

## Anomalies
Telegram `NetworkError: httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known` in bot.log. Transient DNS failure — self-resolved (weekend summary sent successfully at 10:00:07 before the blip, retry loop recovered). Not blocking.

bot.err is entirely `MallocStackLogging: can't turn off malloc stack logging because it was not enabled` — benign macOS noise, not actionable.

## Recommended actions
1. **gate_weekly_trend N=1**: no action now. When N≥20, run `gate_false_negative_rates()` and evaluate loosening the weekly-trend block.
2. **Telegram DNS blip**: if recurs on a trading day (retry loop exhausts during market hours), check local DNS resolver or add fallback nameserver. Today's instance was transient and harmless.