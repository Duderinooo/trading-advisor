# Health Report 2026-05-29T20:44:21+02:00

## Overall: 🟡 degraded
3OIL.MI yfinance fetch stalled 528s, risking full price-check cycle blockage. All other signals nominal.

## Pipeline freshness
Heartbeat 41s old, fresh. `api_calls_today=7` — low but consistent with heavy GEO-dedup suppression (3OIL.MI Iran news suppressed 6× from single 14:46 fire) + no open trades requiring exit monitoring + AIR.DE skipped as irrelevant. No gap anomaly.

## LLM channel
agent_runs.db: 4× bug-watcher runs today (ids 203–206), all exit_code=0, error=null. opening + morning + event traces all present, warnings=[]. 7 Claude API calls total — all Haiku, no Sonnet calls visible in recent runs (morning would have been earlier). No errors.

## Data quality
`2026-05-29 18:06:44` — 3OIL.MI yfinance fetch failed after **528,281ms (8.8 minutes)** via curl. This is not a timeout clamp — curl ran uninterrupted for ~9 min before failing with 0 bytes received. If `_fetch_ticker` for 3OIL.MI runs inline during price check, one stall of this magnitude can freeze the entire 15-min polling cycle.

`2026-05-29 16:57:41` — LS-TC quote fetch for DE000CBK1001 timed out at 3s (read_timeout). Single occurrence, transient.

Earlier in log (timestamp not visible in 80-line tail): Telegram `httpx.ConnectError: [Errno 8] nodename nor servname provided` — DNS failure. Bot recovered (subsequent log activity confirms).

## State integrity
bot.db + agent_runs.db: `PRAGMA integrity_check` → ok. bot.err contains only `MallocStackLogging: can't turn off malloc stack logging because it was not enabled` — benign macOS subprocess diagnostic, not Python errors.

## Gate behavior
`gate_weekly_trend`: FNR=1.0 (N=1, would_win=1). One blocked entry would have won. **N=1 is below the N≥20 tuning floor** — no action warranted yet. Begin tracking; revisit when N reaches 5–10 for directional signal.

`gate_no_entry_zone`: 73 blocks, 0 would-win, FNR=0.0. Healthy.

## Recommended actions
1. **Investigate 3OIL.MI curl stall**: Check if `_fetch_ticker` has a curl/yfinance-level timeout clamped below the 528s observed. `grep -n "timeout" core/data/market_data/*.py` — if no hard timeout on the yfinance call itself, add one (e.g. `timeout=10` in `yf.download`/`Ticker.history` call). The LS-TC 3s timeout exists; yfinance path apparently doesn't have one. This is the single highest-priority fix.
2. **Monitor `gate_weekly_trend` FNR**: Currently N=1, no tuning warranted. If it reaches N=5 with ≥3 would-win, cross-check against `compute_hit_stats` before considering `MIN_RS_20D_VS_INDEX_PCT` or weekly-trend threshold review.
3. **Telegram DNS blip**: Transient; `python-telegram-bot` already retries via `network_retry_loop`. No action unless recurrence increases.