# Health Report 2026-06-04T12:07:44 CET

## Overall: 🔴 critical
Two LLM call failures (`error_max_structured_output_retries`) blocked XETRA opening check + price-alert event analysis; morning Sonnet skipped `set_watch_levels`.

---

## LLM channel

**08:10:13 — event mode (haiku) FAILED**
`error_max_structured_output_retries` after 11 turns, $0.11 cost burned. Triggered by 3OIL.MI/3OIS.MI +11%/-10% big-mover bypass.

**09:12:21 — opening check XETRA FAILED**
Same error, 15 turns, $0.16 cost burned. Bot never got an opening analysis today.

Pattern: haiku exhausts structured-output retries when context is large (501k cache-read tokens on opening call). This is a recurring failure mode — previous incidents document the same subtype.

**Morning Sonnet: `set_watch_levels NOT called`** (ERROR at 08:03:09)
`out_tok=6059` against a 3500-token budget display. Sonnet ran 148s, produced analysis text for DBK.DE, but never called the tool. Watch levels not refreshed today.

`api_calls_today=1` confirms only morning counted; event+opening both failed before completion.

---

## Pipeline freshness

Heartbeat age 52s — loop alive. But from 08:10 onward no successful Claude call. 3.75h of market hours without LLM analysis. News dedup still firing (geo events suppressed correctly), but no actionable analysis possible on new events.

---

## Gate behavior

`gate_weekly_trend` FNR = **1.0** (1/1 blocked would have won). N=1, below MIN_SAMPLE_SIZE_FOR_TUNING=20 — not tunable yet, but worth tracking. First datapoint for this gate.

`gate_no_entry_zone` FNR = 0.0 across 74 blocks — healthy.

---

## Open-position aging

DBK.DE entered 2026-06-03 08:06 (~28h). Not stale. SL monitoring still runs under failure (kill-switch exemption holds).

---

## Recommended actions

1. **Investigate `error_max_structured_output_retries` root cause** — check `agents/_lib/runner.py` retry logic and tool-schema enforcement. Haiku is hitting this consistently when context is large (opening: 501k cache-read tokens, 49k cache-creation). Likely haiku fails to emit valid tool JSON after many turns. Previous incidents should have a fix candidate — check `docs/incidents/2026-06-04-0900-bug-watcher.md`.

2. **Fix morning `set_watch_levels NOT called`** — Sonnet completed `end_turn` without calling the tool. Either: (a) prompt doesn't force tool call for morning mode, or (b) `force_any_tool` not set for morning. Check `core/llm/analyzer.py` morning config block. Add `force_any_tool=True` or validate post-run that watch levels were updated.

3. **Manually trigger opening check** — `/morning` or check if `/opening` command exists. DBK.DE position unmonitored by LLM since yesterday's close.

4. **`set_watch_levels`** wasn't called — current watch levels are stale (pre-2026-06-04). Any 3OIL/3OIS zone entries based on yesterday's levels may be miscalibrated.

5. **bot.err `MallocStackLogging`** — benign macOS noise, not actionable.