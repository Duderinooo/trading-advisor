# 2026-04-27 — Watch self-sabotage + triple-message bug (RWE.DE)

## Symptom

RWE.DE breakout @€60.6 — entry filled, position open with TP1=€62.4.
Sonnet's morning brief also set `watch_resistance_reject @€60.7` (a defense
watch in case the breakout failed). 15min snapshot caught the natural tag-and-
continue: price tagged €60.85, dipped to €60.65. Bot saw "below resistance_reject
trigger" → fired `WATCH_LEVEL_HIT` → analyzer recommended EXIT on the active
breakout-position. Triple-message: ENTRY rec, news rec, EXIT rec all firing
within 20 minutes on the same ticker.

## Root cause

1. **Watch-self-sabotage:** a `resistance_reject` level placed *between* an
   open position's entry-price and its TP1 turns the natural up-move into an
   exit-trigger. Bot fires the level on tag-and-dip.
2. **No direction-confirm:** the proximity check was symmetric (abs(price-trigger)).
   A `resistance_reject` should only fire when price actually crosses back
   *below* the trigger, not just touches it.
3. **GATE_BLOCK_NOTIFY noise:** entry-blocked alerts going to Telegram caused
   triple-message on a single ticker (entry rec + block + news).

## Fix

1. **Self-sabotage filter** in `core/llm/analyzer.py` `_persist_watch_levels`:
   drop incoming `resistance_reject` levels whose trigger sits between an open
   trade's entry and TP1.
2. **Direction-confirm buffer** in `core/events/watchlevels.py`:
   `resistance_reject` only fires when `current_price ≤ trigger - buf_abs`
   (0.25×ATR buffer or 0.3% absolute). Same direction-flip for `support_bounce`.
3. **GATE_BLOCK_NOTIFY whitelist:** Only `risk_halt` blocks go to Telegram;
   all other gate blocks log to dashboard only.

## Constants

| File | Constant | Value |
|---|---|---|
| `config/execution.py` | `GATE_BLOCK_NOTIFY_WHITELIST` | {"risk_halt"} |

## Lessons

- **Defense watches need spatial awareness.** A `resistance_reject` *between*
  entry and TP1 is self-sabotage by design; not just a "be careful" — drop it.
- **Direction matters.** Proximity-checks without direction-flip miss the
  semantic difference between "touched" and "broke through". Always buffer
  by 0.25×ATR or %-absolute.
- **Telegram is high-cost.** Only portfolio-wide safety pages the user;
  individual gate-block noise belongs in the dashboard.
