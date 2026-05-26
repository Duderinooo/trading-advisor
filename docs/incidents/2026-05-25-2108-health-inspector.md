# Health Report 2026-05-25T21:07:31+02:00

## Overall: 🟢 healthy
Process stable 7h52m, heartbeat 1s fresh, both DBs clean, no stderr. Transient ghost instance at 09:09 self-resolved.

## Process hygiene
Clean process tree: `run.sh`(17232) → `caffeinate -i`(17234) + `main.py`(17236) + `caffeinate -is -w 17236`(17237). Proper double-caffeinate wrap. No orphans.

**Note:** At 09:09:37, startup rejected itself: `sibling main.py instances detected: [36841]`. PID 36841 was a stale ghost from the prior 09:03 restart. Resolved without intervention — 09:10 restart succeeded cleanly. Current session has been stable since 13:15:39 (7h52m).

## Pipeline freshness
Heartbeat age=1s at report time. All four expected pipeline stages completed today:
- Morning trace: ✓
- XETRA opening (09:05→06): ✓ (pre-config-change timing)
- US opening (15:35→36): ✓
- Event checks: ✓ (14:16, 16:01, 20:18)

6 bot restarts between 09:00–13:15 before current stable session. No error-driven restarts — pattern matches user-initiated config deploys (commit `952e907` pushed XETRA opening 09:05→09:10; confirmed in 13:15 boot log).

## LLM channel
All agent runs exit_code=0. Zero errors in agent_runs.db tail (50 entries).

Model routing correct throughout: haiku for event/opening/bug-watcher, sonnet for health-inspector.

`api_calls_today=10` is consistent with: 1 morning (sonnet) + 2 opening (haiku) + 3 event (haiku) + health-inspector runs — no runaway calls.

## Data quality
**Persistent liquidity suppression:** Every afternoon event check drops 4–5 watchlist tickers:
- `AIXA.DE`, `DHL.DE`, `RWE.DE`, `SMHN.DE` — vol_ratio < 0.30 (event-mode threshold)
- `4GLD.DE` — price €125–126 > €100 whole-share cap (structural, known)
- `3OIL.MI` — spread >2% (structural)

This leaves Claude seeing only `IFX.DE`, `CBK.DE`, `PUM.DE`, `DBK.DE` (partial) in afternoon event mode. Not a bug, but worth tracking: if AIXA/DHL/RWE never breach 0.30 vol_ratio in the afternoon, they're effectively blind spots post-opening.

**GEO dedup working correctly:** 3OIL.MI Iran-deal news storm (20+ articles) properly anchored at 08:02 then 14:16 then 20:18, with all intermediates suppressed.

## Open-position aging
2 positions: BAYN.DE, MBG.DE. Earliest entry 2026-05-21 13:35 — 4 calendar days. Not stale-thesis territory yet (typical `hold_days_max` ~5–10d). Monitor Wednesday if thesis unchanged.

## Gate behavior
`gate_false_negative_rates()` returned `{}` — no blocked-entry counterfactuals recorded today. Either no entries were blocked (plausible: both opening checks + all event checks returned `tool_called=False, raw_levels=0` — Haiku passed on all setups) or `record_blocked_entry` had nothing to write.

Cross-signal: `raw_levels=0` on both opening checks means no watch levels were emitted. No new entries proposed all day. Consistent with 2 open positions + DAX at record highs (25k+) + liquidity gate suppressing half the watchlist.

## Anomalies
**`tool_called=False` everywhere:** All 5 Claude calls today (2 opening, 3 event) emitted zero tool calls. Haiku analyzed the market and decided PASS on every cycle. This is internally consistent given: (a) 2 positions already open, (b) DAX at record high → likely extended, (c) heavy liquidity suppression reducing opportunity set. Not flagged as a problem, but if tomorrow's morning prep also produces zero levels, investigate whether regime gate or heat cap is implicitly blocking everything.

**`market_hours=true` at 21:07 CET:** US session runs until ~22:00 CET. Correct.

## Recommended actions
1. **Monitor AIXA.DE/DHL.DE/RWE.DE vol suppression:** Query `SELECT date, vol_ratio FROM market_snapshots WHERE ticker IN ('AIXA.DE','DHL.DE','RWE.DE') ORDER BY ts DESC LIMIT 50` to check if afternoon vol_ratio is chronically sub-0.30. If so, these tickers only get coverage at XETRA open — consider whether that's acceptable or warrants a mode-specific vol_min override.
2. **Check BAYN.DE/MBG.DE thesis freshness Wednesday:** Entry was 2026-05-21. If still open by 2026-05-28, verify `hold_days_max` not breached and stale-thesis alert hasn't fired silently.
3. **Ghost-instance RCA (low priority):** The PID 36841 at 09:09 implies the 09:03 shutdown didn't fully reap its Python process within the ~6s restart gap. Not critical since self-detected, but if restarts happen again in quick succession (< 10s), add a `pkill -f main.py` step or extend the pre-start grace period in `run.sh`.

<!-- bug-worker-status: processed ts=2026-05-26 reason=already-fixed-manually -->
