# 2026-05-07 — Brier-haircut cap + event-dedup TTL (CON.DE incident)

## Symptom

CON.DE up +9% intraday. Bot identified the setup, generated `recommend_entry`,
but edge-gate blocked it at edge=-0.005 (below `MIN_EXPECTED_EDGE=0.04`).

The block was wrong: Claude's `p_win` was 0.58, but the Brier-haircut on
`(avg_p_predicted - actual_win_rate)` was -0.38 (bot historically pessimistic).
Without haircut, `p_adj` should have been `0.58 - (-0.38) = 0.96` (capped to
0.99). Edge would have passed comfortably.

Code silently ignored haircut → gate saw `p_raw=0.58` unadjusted → blocked.

After the block, the watch-level's dedup-key was marked-as-fired for the **full
day** (per-day dedup). Subsequent watch-hits on CON.DE were suppressed even
after we fixed the gate. Lost the rest of the run.

## Root cause

1. Edge gate computed `p_adj = p_raw` instead of `p_raw - capped_haircut`.
2. Watch-dedup used per-day key. Once a watch-hit was consumed (even by a
   subsequent gate-block), the watch went blind for the rest of the day.

## Fix

1. Cap haircut at ±0.20 (small-sample noise floor) and apply bidirectionally:
   - `p_raw` ≥ haircut > 0 → subtract (Claude over-confident, tighten)
   - `p_raw` < haircut < 0 → add (Claude under-confident, loosen)
2. Replace per-day watch-dedup with TTL-based:
   `EVENT_DEDUP_TTL_MIN = 60` → polite re-fire after 60min cooldown if first
   hit got dropped by a downstream gate.
3. Bumped `BREAKOUT_TRIGGER_PERCENT` 0.5 → 1.0 — 3OIL.MI -20.9% intraday was
   0.56% over trigger, missed window with 0.5% slack.
4. Added `CONFIRM_CLOSE_TOLERANCE_PCT = 0.1` — INL.DE 94.47 vs 94.48 (Sonnet's
   confirm_close_above=94.48): 11× tag-and-no-confirm. 0.1% slack = €0.094 at
   €94.20 trigger, well within real-life spread/tick granularity.

## Constants

| File | Constant | Old | New |
|---|---|---|---|
| `core/llm/handlers/gates/quality.py` | `HAIRCUT_CAP` (local) | n/a | 0.20 |
| `config/execution.py` | `EVENT_DEDUP_TTL_MIN` | per-day | 60 |
| `config/execution.py` | `BREAKOUT_TRIGGER_PERCENT` | 0.5 | 1.0 |
| `config/execution.py` | `CONFIRM_CLOSE_TOLERANCE_PCT` | n/a | 0.1 |

## Lessons

- **Bidirectional calibration matters.** Bot pessimism is just as costly as
  over-confidence. Don't bake in one-sided corrections.
- **Cap small-sample bias.** ±0.20 prevents runaway haircuts from <20 trades.
- **TTL-dedup > per-day-dedup.** A first hit dropped by a downstream gate
  shouldn't disable the watch for the full session.
- **Threshold-slacks for tick granularity.** Comparing 15min-delayed yfinance
  data against tight (0.01€) confirm levels needs absolute or %-slack to avoid
  semantic-equivalent misses.
