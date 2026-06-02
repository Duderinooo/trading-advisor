# Health Report 2026-06-01T02:22 CET

## Overall: 🟢 healthy
Transient DNS outage at 19:04 self-healed; all traces clean; process intact; 0 open trades.

## Process hygiene
PID chain clean: `run.sh(1881)` → `caffeinate -i(1901)` → `main.py(1915)` → `caffeinate -is -w 1915(1916)`. No orphans, no duplicate instances, caffeinate present on both shell and process.

## Pipeline freshness
Heartbeat age 4.4h (`last_tick=21:59:13`). Market closed (02:22 CET), Sunday holiday weekend — gap is expected. `api_calls_today=1` consistent with off-hours.

## LLM channel
All 4 traces present (morning, opening_xetra, opening_us, event), zero warnings. `eod-postmortem` ran at 22:00 with `result_chars=17, input_tokens=3` — near-empty output, consistent with 0 open trades (nothing to postmortem). Not an error.

## Data quality
DNS outage at `2026-05-31 19:04:44` hit both Telegram and LS-TC simultaneously — clear transient network event, not bot bug. Bot auto-recovered: news monitor active at 20:22, 20:37, 21:37, 21:52. LS-TC quotes failed for `IE00BMTM6B32` and `DE000CBK1001` during that window only.

## State integrity
`bot.db` and `agent_runs.db`: `ok`. `bot.err` contains only macOS `MallocStackLogging` noise — harmless, no real errors.

## Gate behavior
`gate_weekly_trend` FNR=1.0 at **N=1** (1 blocked trade, 1 would-have-won). Below `MIN_SAMPLE_SIZE_FOR_TUNING=20` — no action warranted, watch for accumulation.

## Recommended actions
1. **Monitor LS-TC DNS failures**: if `www.ls-tc.de` resolution keeps failing intermittently, check whether macOS DNS resolver (mDNSResponder) needs a flush (`sudo dscacheutil -flushcache`) or router/VPN is dropping foreign hostnames. No code change needed yet.
2. **Watch `gate_weekly_trend` FNR**: currently 1/1 = 100%. Log accumulates automatically. Re-evaluate once N≥20 via `gate_false_negative_rates()`.