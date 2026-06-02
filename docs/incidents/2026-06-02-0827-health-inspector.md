# Health Report 2026-06-02T08:26:56 CET

## Overall: 🟢 healthy
Process clean, heartbeat 11s fresh, DBs intact, all agents exit_code=0. One informational: morning did not set watch levels (deliberate macro-block, but has post-release implications).

## Pipeline freshness
Heartbeat age 11s at market-open — nominal. Morning prep ran 08:00 (125s, within tolerance). Event call fired 08:10 on SAP +8.1% / 3OIL +18.6% big-mover bypass.

## LLM channel
All agent_runs exit_code=0, no API errors. Model mix correct: morning=sonnet, event/bug-watcher=haiku. Morning duration 125,819ms — elevated but not alarming (tracefour + full context load).

## State integrity
`bot.db` + `agent_runs.db` integrity_check both `ok`. MallocStackLogging lines in bot.err are macOS Python 3.14 noise — benign.

## Open-position aging
0 open trades.

## Gate behavior
`gate_weekly_trend` FNR=1.0 (1 block, would have won). N=1 — below `MIN_SAMPLE_SIZE_FOR_TUNING=20`. Not actionable, but worth watching as sample grows.

`gate_no_entry_zone` FNR=0.0 across 73 blocks — functioning correctly.

## Anomalies
**Morning PASS + no watch levels set.** Trace: `stop=end_turn, tool_called=False, raw_levels=0`, log ERROR `set_watch_levels NOT called`. Text preview: *"PASS. EU Inflation Flash 11:00 blocks all EU entries (pre-release)."* — Sonnet's reasoning is sound, behavior is intentional. However, opening_check already ran (auto-only, no force flag). After EU Inflation Flash releases at 11:00 CET, no automated mechanism will re-evaluate and set watch levels. Bot will run event/news detection but with zero active watch levels for the remainder of today's session.

**SMHN.DE liquidity-gated at event call** (spread=0.961% > max). Was morning's "best candidate". Spread may normalize intraday.

## Recommended actions
1. **After 11:00 CET**: run `/morning` to let Sonnet re-evaluate post-inflation-release and potentially set watch levels for afternoon session. Without this, price-alert / event detection fires but no watch-level proximity triggers are active today.
2. **Telemetry logging level**: `set_watch_levels NOT called` logged as ERROR is misleading when Sonnet deliberately PASSes. Consider logging as INFO with `reason=macro_block` to reduce alarm fatigue. Evidence: this pattern appeared before (2026-06-01 report).
3. **Monitor SMHN.DE spread** intraday — if it drops below 0.3% and Sonnet re-runs after 11:00, it becomes entry-eligible again.