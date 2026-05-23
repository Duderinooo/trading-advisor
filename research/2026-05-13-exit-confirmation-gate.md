# 2026-05-13 — Exit-confirmation gate (PUM.DE whipsaw)

## Symptom

PUM.DE position: bot recommended EXIT at €24.58 citing RS-collapse + RSI=38
(oversold approaching). User confirmed sell. Next day +4.8% rip; TP1 of €26.73
nearly tagged. Net: locked a small loss instead of letting position recover.

## Root cause

Thesis-decay exit-recs fired on intraday-noise without multi-signal
confirmation. RS-deterioration and MA-break are leading-but-noisy signals — a
single intraday-bar of weakness isn't a real "thesis broken" event.

## Fix

Multi-signal **exit-confirmation gate** in `core/llm/handlers/recs/exit_rec.py`
(`exit_thesis_decay_confirmed`). Soft-suppress thesis-decay exits when ANY of:
- `vol_ratio < EXIT_GATE_MIN_VOL_RATIO` (thin selloff = no capitulation)
- `rsi14 < EXIT_GATE_MIN_RSI` (oversold-bounce-zone → wait)
- `vwap_dev_atr ∈ [EXIT_GATE_MAX_VWAP_DEV_ATR, 0]` (mild dip, not panic)

Hard exits (SL-hit, earnings-defense, TP-hit, panic) bypass the gate
unconditionally — soft only applies to LLM-emitted thesis-decay exits.

If the signal persists, bot calls `recommend_exit` again next bar. So this is
a polite delay, not a hard block.

Coupled with **two-stage suppress** in `core/llm/handlers/persist.py`:
1. **Cooldown after auto-drop:** EXIT_REC_COOLDOWN_MIN_AFTER_DROP = 240min
   prevents Claude from re-issuing same exit minutes after user ignored it.
2. **Keep-existing:** if pending exit-rec already exists for ticker, don't
   replace (timer reset would re-ping user via Telegram).

## Constants

| File | Constant | Value |
|---|---|---|
| `config/execution.py` | `EXIT_GATE_MIN_VOL_RATIO` | 0.5 |
| `config/execution.py` | `EXIT_GATE_MIN_RSI` | 35 |
| `config/execution.py` | `EXIT_GATE_MAX_VWAP_DEV_ATR` | -0.5 |
| `config/execution.py` | `EXIT_REC_COOLDOWN_MIN_AFTER_DROP` | 240 |
| `config/execution.py` | `EXIT_AUTO_DROP_GAP_MIN` | 60 |

## Lessons

- **Exit-recs are higher-stakes than entries.** A bad exit locks loss; a bad
  entry can be vetoed at /confirm. Bias toward "wait" on soft signals.
- **Multi-signal confirmation > single-signal trigger** for whipsaw-prone
  decisions.
- **State-machine for reminders.** First reminder → gap → auto-drop + cooldown
  + suppression. One ping, not three.
