# 2026-05-04 — Big-mover bypass (NVD/AMD-gap missed)

## Symptom

NVD.DE gapped -8% pre-XETRA-open on overnight earnings miss. Morning prep at
08:00 ran the full cycle including a forced analysis. Cooldown clock then
prevented any new analysis until 08:45.

At 09:05 XETRA opened, NVD.DE was -7.2% on the day. Price-alert fired. But
cooldown blocked the analyzer call. The gap was completely swallowed — bot
silently sat through a -7% open on a position-held ticker.

## Root cause

`MIN_MINUTES_BETWEEN_ANALYSES = 45` is correct for the swing-style normal
case (no hectic), but doesn't account for **gap-magnitude**. A 7% pre-open
gap is qualitatively different from a normal 1-2% intraday move.

## Fix

**Big-mover bypass** in `core/llm/telemetry/api_usage.py can_make_api_call`:
when the calling code passes `bypass_cooldown=True` (and the price-alert
flow sets this when `|change| ≥ BIG_MOVER_PCT_BYPASS`), even the forced-
cooldown is overridden.

```python
BIG_MOVER_PCT_BYPASS = 4.0  # |change| ≥ 4% breaks cooldown
```

Daily cap still applies. Just the inter-call cooldown is bypassed for
big movers.

## Constants

| File | Constant | Value |
|---|---|---|
| `config/execution.py` | `BIG_MOVER_PCT_BYPASS` | 4.0 |

## Lessons

- **Magnitude-aware rate-limiting.** Generic cooldowns are too coarse for
  asymmetric events. Big moves get bypass; small moves stay rate-limited.
- **Pre-open data matters.** XETRA opens 1h after morning prep — between
  08:00 and 09:00 a US-overnight earnings reaction can fully drain a
  position. Bot must be allowed to react.
- **Cooldown still caps daily spend.** Bypass is per-call, not global.
