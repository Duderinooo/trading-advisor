# Health Report 2026-06-02T20:28:57+02:00

## Overall: 🟢 healthy
Process clean, heartbeat fresh (53s), DBs ok, no application errors. All agent runs exit_code=0.

## Process hygiene
```
57181  run.sh (elapsed 02:58:11, PPID=1)
57182  caffeinate -i (run.sh wrapper)
57184  python main.py (child of 57181)
57185  caffeinate -is -w 57184 (python guard)
```
Single instance. PGID consistent. Caffeinate double-wrap expected (run.sh + main.py own guard). Graceful restart at 17:30–17:31 CET logged cleanly.

## Pipeline freshness
Heartbeat age 53s at snapshot. Market hours active. 10 api_calls_today — consistent with observed Claude calls: morning(1) + XETRA open(1) + US open(1) + ~7 event triggers. No gaps.

## Data quality
LS-TC quote fetch timed out twice (13:09, 13:10) for ISIN `IE00BMTM6B32`. Ticker not in current watchlist — likely stale watch_level residue. Transient, no downstream impact visible.

Whole-share gate dropping `4GLD.DE`, `SAP.DE`, `AIR.DE` on every event call (price > €100 cap). Consistent and expected; not a regression.

## LLM channel
All 4 traces (morning, XETRA opening, US opening, event) show `tool_called=False, raw_levels=0`. Every Claude call today returned PASS — including IFX.DE +7.7%, SMHN.DE +7.2%, 3OIS.MI +5.6% big-mover events. This is consistent with extended-up-day gate blocking entries on >1.5×ATR14 moves. No LLM errors, no timeouts, no malformed outputs. `out_tok=6280/900` on the opening trace is high but not alarming (full context window used for analysis).

## Gate behavior
`gate_weekly_trend`: N=1, FNR=1.0 (the 1 blocked trade would have won). Below `MIN_SAMPLE_SIZE_FOR_TUNING=20` — **no tuning warranted**, but first data point in the record. Monitor accumulation.

`gate_no_entry_zone`: 73 blocks, 0 FNR. Working correctly.

## Anomalies
`IE00BMTM6B32` appearing in LS-TC fetch attempts but absent from watchlist/portfolio. If this is a dead watch_level, it will generate spurious timeout noise on every polling cycle.

## Recommended actions
1. **Identify IE00BMTM6B32 stale entry**: run `SELECT * FROM kv_state WHERE value LIKE '%IE00BMTM6B32%'; SELECT * FROM triggered_price_alerts WHERE ticker='IE00BMTM6B32'` against bot.db to find where this ISIN is referenced. If it's a dead watch_level in portfolio.json, remove it via the normal watch_level edit path.
2. **Watch `gate_weekly_trend` FNR**: currently N=1 (insufficient for tuning), but log this as the first data point. If it reaches N≥5 with FNR≥0.6, start a research note.