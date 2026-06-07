Needs write permission for docs folder. Approve when ready — or I can paste the content directly here.

**Key findings this W23 run:**

**No threshold changes.** N=6 closed trades total (need 20).

**3 pipeline bugs blocking analytics:**

1. **BUG-1 (HIGH)** — context-gather crash `'str' object has no attribute 'get'`: same bug as W22, not yet fixed. Weekly calibrator passes full portfolio dict to `compute_hit_stats()` instead of `closed_trades` list.

2. **BUG-2 (HIGH)** — `gate_outcomes.jsonl` dedup failure: single BAS.DE block (entry €53, SL €52) written **75 times** across 2026-05-23→06-05. FNR stats for `gate_no_entry_zone` are useless until dedup is added to `record_blocked_entry()`.

3. **BUG-3 (MEDIUM)** — `calibration_history.jsonl` `n_scored` always null. Also `decisions.jsonl` has all-placeholder timestamps (2026-01-01).

**Calibration status:**
- Brier 30d: 0.238, p_win bias: +6.8pp (overconfident), but N=4 < MIN_CALIBRATION_N=10 → haircut stays 0, no manual action needed

**One watch-list item:** gate_weekly_trend blocked DBK.DE pre_breakout_squeeze on 2026-05-25; next day peaked +6.69%, would have hit TP1. N=1, not actionable — but worth watching if more blocks accumulate.

Approve the file write and I'll also fix BUG-1 if you want.