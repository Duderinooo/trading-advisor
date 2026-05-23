# 2026-05-13 — Brier-haircut activation floor (MIN_CALIBRATION_N=10)

## Symptom

Bot state: n=1 scored trade (single Brier sample). `bias = avg_p_pred -
actual_win_rate = -0.38` (from that one sample). Without floor, every
subsequent `p_win` got +0.20 (capped) added — a single sample drove a
20-percentage-point shift to **every entry rec**.

## Root cause

`compute_hit_stats` returned a non-zero `haircut` even on n=1. Edge-gate
applied it. Bias-from-noise overrode all Claude's calibration work.

## Fix

```python
MIN_CALIBRATION_N = 10  # Min scored trades for haircut activation
```

Below 10 scored trades, `haircut=0.0`. Above, gradually-confident correction.

## Constants

| File | Constant | Value |
|---|---|---|
| `config/risk.py` | `MIN_CALIBRATION_N` | 10 |

## Lessons

- **N=1 calibration is noise.** Calibration corrections need statistical
  footing — minimum sample size before activation.
- **Connect to overfitting policy** (research/2026-05-12-edge-floor-006.md
  rule): no threshold tuning on N=1. Same idea applies to live-applied
  calibration corrections, not just config edits.
- **Codified as policy:** `MIN_SAMPLE_SIZE_FOR_TUNING = 20` in `config/risk.py`
  — any constant retune needs ≥20 outcome samples.
