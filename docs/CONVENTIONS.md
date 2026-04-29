# Conventions

Short list of things that look arbitrary but aren't.

## Naming

- German inline comments and user-facing strings are intentional — single user, German-native. Don't translate to English during refactors.
- `ticker` is always the XETRA symbol (e.g. `NVD.DE`). US-style symbols live only in `TICKER_ALIASES`.
- `shares` is always `float` (TR Bruchstücke), never `int`.
- Money fields end in `_eur`, percentages end in `_pct` or `_percent`, ratios have no suffix.
- Dates stored as ISO strings (`"2026-04-24"` or `"2026-04-24 12:35"`), not `datetime` objects — JSON-friendly.

## Portfolio.json schema (contract)

```
{
  "cash_eur": float,
  "total_capital_eur": float,
  "open_trades": [trade],
  "closed_trades": [closed_trade],
  "pending_recommendations": [rec],
  "watch_levels": [{ticker, type, trigger_price, note}],
  "triggered_events": [{key, date, time}],
  "triggered_price_alerts": [{key, date}],
  "seen_news": {date: [md5_prefix]},
  "kill_switch": bool,
  "kill_switch_reason": str,
  "kill_switch_ts": str,
  "last_morning_prep_date": str,
  "last_xetra_open_check_date": str,
  "last_us_open_check_date": str,
  "last_analysis": str,
  "last_updated": str
}
```

Trade record keys:

```
ticker, entry_price, shares, size_eur,
stop_loss, take_profit (scalar | list),
trailing_stop_pct, conviction, p_win, thesis,
entry_date, status ("open" | "closed"),
rec_entry_price, slippage_pct,
# on close:
exit_price, exit_reason, exit_date,
pnl_eur, pnl_pct, brier, outcome,
mistake_tag, mistake_class
```

**Backward compatibility:** old trades may lack any of the newer fields (`rec_entry_price`, `slippage_pct`, `mistake_tag`, `mistake_class`). Always `.get(...)` with a sensible default — never index directly.

## Error handling

- Network/yfinance calls: `try/except Exception`, log at `warning`, return empty collection. Never let a single ticker fetch crash the loop.
- MemPalace: every helper catches its own errors and logs at `warning`. Bot must work fully without MemPalace.
- Telegram: Failure to send is already logged in `notifier`. Don't add retries here — the next tick will re-evaluate state.
- Claude API: let it raise. The surrounding `run_*_check` function has a broad `except Exception` that reports via `send_alert`.

## Logging

- `INFO` — control-flow milestones (check started, analysis sent, rec created)
- `WARNING` — halted entries, slippage rejects, self-calibration suggestions, dropped extreme spikes
- `DEBUG` — noisy per-ticker detail (indicator-calc failures, price-alert filtered)

Use `logger.exception` only in the `except` arms of the top-level run functions — not in helpers.

## Prompt engineering

- Format-enforcer header is prepended per mode because Claude ignores system-prompt format rules ~30% of the time.
- `stop_sequences` are the last line of defense against "Internal Analysis:" style prefixes.
- Any new section added to `analysis_request` must be cache-stable (no timestamps inside the system prompt; timestamps go in the user message).
- Cached blocks: `system` uses `cache_control: ephemeral`. Adding tool definitions is cache-safe; changing their order is not.

## When to call Claude vs. deterministic code

Deterministic:
- All indicators, gates, halts
- Event detection and dedup
- Notification routing

Claude:
- Setup evaluation + A+ scoring
- Thesis composition
- Portfolio-level judgment (close vs. hold vs. add)

Never call Claude for things a deterministic check can answer — latency, cost, and cache-bust risk.
