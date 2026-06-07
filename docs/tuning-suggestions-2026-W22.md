Fix: line 251 passes portfolio dict to `compute_hit_stats` instead of `closed_trades` list. Dict iterates as string keys → `str.get()` → crash.

Proposed change:
```python
# before
p = load_portfolio()
hit = compute_hit_stats(p)           # BUG: dict, not list
fnr = gate_false_negative_rates()
closed = p.get("closed_trades", [])

# after
p = load_portfolio()
closed = p.get("closed_trades", [])  # extract first
hit = compute_hit_stats(closed, p.get("cash_movements"))
fnr = gate_false_negative_rates()
```

Approve the edit?