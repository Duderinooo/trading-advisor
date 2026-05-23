# 2026-05-07 — Breakout-volume floor too strict for XETRA mid-caps

## Symptom

INL.DE @€94.20 — Sonnet's morning watch-level: `breakout_resistance`. Price
crossed trigger intraday. Gate check: `vol_ratio=0.34` (current vol vs daily
avg, where daily-avg includes the typical low-liquidity afternoon hours).
Gate threshold `MIN_BREAKOUT_VOLUME_RATIO = 1.3`. Fail. Watch suppressed.

Same day INL.DE confirmed the breakout with a +6% move. Bot missed it.

## Root cause

`MIN_BREAKOUT_VOLUME_RATIO = 1.3` was tuned to US-large-cap behavior (where
1.3× avg-volume on breakout is the empirical floor). XETRA mid-caps trade
thinner, and **early-day volume is structurally below daily avg** because
the avg includes 8 hours of low-volume European afternoon. A 0.34 ratio at
10:00 is "above-average for this time of day" even though below the daily
mean.

## Fix

Lower floor to **1.0**:

```python
MIN_BREAKOUT_VOLUME_RATIO = 1.0  # was 1.3
```

Above-average volume (any positive deviation) is sufficient for confirm
on these liquidity profiles. The 1.3 floor was a US-equity heuristic
ill-fit for XETRA.

## Constants

| File | Constant | Old | New |
|---|---|---|---|
| `config/execution.py` | `MIN_BREAKOUT_VOLUME_RATIO` | 1.3 | 1.0 |

## Lessons

- **Volume ratios are time-of-day biased.** Daily-avg denominator includes
  off-peak hours. Early-day ratios are systematically low.
- **Calibrate to your market.** US-large-cap thresholds don't transfer to
  XETRA mid-cap.
- **Don't import generic "breakout" rules from US trading literature
  unchanged.** Volume structure is venue-specific.
