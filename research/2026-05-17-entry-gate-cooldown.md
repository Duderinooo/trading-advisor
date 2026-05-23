# 2026-05-17 — Entry-gate cooldown (BAS.DE re-block storm)

## Symptom

BAS.DE failed the RS gate at 09:15 (rs_20d=-2.3pp < 0.0 floor). Re-evaluated
at 10:08 → same RS → blocked again. Re-evaluated 11:12 → same → blocked.
3× edge-block within 2h, each burning a Haiku event-call. The market_data
soft-hint (Claude saw "RS=-2.3pp") didn't stop the re-call because Claude's
prompt-pattern is "evaluate any qualifying watch-hit".

## Root cause

`rs_20d` is **intraday-stable** — it doesn't change minute-to-minute. Same for
`edge` (snapshot of p_win × payoff). When these gates fail, the underlying
condition won't change within the hour. But the event-detector kept firing
fresh watch-hits, each triggering a fresh Haiku call.

## Fix

Add **entry-gate-cooldown** marker on the ticker's market_data dict. When a
ticker fails one of the intraday-stable gates (RS, edge, red-team), record
the cooldown timestamp + gate name + reason in portfolio.json.

Two effects:
1. `core/events/watchlevels.py detect_events` skips the ticker entirely
   during the cooldown (no Haiku call gets fired).
2. If somehow a hit reaches the analyzer, the market_data dump shows
   `entry_cooldown: "edge: edge -0.12 < 0.06 — gate would block (cooldown
   until X)"` so Claude visibly skips re-recommending.

Hard-gate stays as backstop on live data. If RS genuinely improves intraday,
the live re-check accepts the trade (cooldown is hint, not hard block).

```python
ENTRY_GATE_COOLDOWN_MIN = 240  # 4h
```

Gates with cooldown stamping (`record_entry_gate_cooldown` call):
- `gate_edge` (in `core/llm/handlers/gates/quality.py`)
- `gate_rs` (in `core/llm/handlers/gates/quality.py`)
- `gate_red_team` (in `core/llm/handlers/gates/cluster.py`)

## Constants

| File | Constant | Value |
|---|---|---|
| `config/execution.py` | `ENTRY_GATE_COOLDOWN_MIN` | 240 |

## Lessons

- **Intraday-stable gates need cooldowns.** Re-evaluating the same condition
  every 15min wastes Haiku calls.
- **Detection-side filter saves more €** than analyzer-side filter. Skip the
  ticker in `detect_events` before generating an event at all.
- **Hint vs hard-block separation.** Cooldown is informational; live gate
  re-checks remain authoritative. Genuine improvements still pass.
- **Red-team verdicts are also intraday-stable.** Same cooldown applies.
