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

---

# Best Practices

This section is the working memory of "stuff we'd otherwise re-derive every time we add a feature." It complements the conventions above with concrete templates, framework gotchas, and pitfalls. Skim it before starting non-trivial work.

## Concurrency / threading

The bot runs **two threads** that both mutate state:

1. **Main loop** — `main.py` event loop, 15-minute ticks. Owns price-checks, event detection, analyzer calls, heartbeat persist.
2. **Telegram listener** — `telegram_listener.start_listener_thread()`, daemon thread. Owns `/confirm`, `/close`, `/panic`, `/morning`, etc.

Three locks coordinate file I/O:

| Lock | File | Module |
|---|---|---|
| `portfolio_lock` (RLock) | `portfolio.json` | `core.portfolio` |
| `paper_lock` (RLock) | `training_portfolio.json` | `core.portfolio` |
| `_usage_lock` (RLock) | `.api_usage.json` | `core.api_usage` |

**Pattern** (every load-modify-save sequence):

```python
with portfolio_lock:
    pf = load_portfolio()
    pf["foo"] = compute_thing()
    save_portfolio(pf)
```

**Never** split this across an `await`, blocking I/O, or a slow third-party call. Hold the lock for the minimum span. If you need to scrape (LS-TC, yfinance, Anthropic), do it OUTSIDE the lock, then acquire the lock to persist:

```python
# ✅ Right: scrape first, then acquire to merge
quotes = scrape_ls_tc(tickers)
with portfolio_lock:
    pf = load_portfolio()
    pf.setdefault("heartbeat", {})["live_quotes"] = quotes
    save_portfolio(pf)

# ❌ Wrong: scrape inside the lock blocks Telegram thread for seconds
with portfolio_lock:
    pf = load_portfolio()
    pf["foo"] = scrape_ls_tc(tickers)   # BAD
    save_portfolio(pf)
```

**Reads without the lock** are tolerated for read-only paths (e.g. `_morning_prep_done_today()`). Stale reads are possible mid-write but never *partial* (atomic-write guarantees the file is always a complete JSON). For decision paths where freshness matters, acquire briefly.

## Atomic file writes

Every state file uses `tempfile.mkstemp` in the same dir + `os.replace`:

```python
def save_portfolio(portfolio: dict):
    portfolio["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    fd, tmp_path = tempfile.mkstemp(
        prefix=".portfolio_", suffix=".json", dir=_PORTFOLIO_PATH.parent
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(portfolio, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _PORTFOLIO_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
```

Why: `os.replace` is atomic on POSIX. A crash mid-write leaves the original intact, never a half-written JSON. Same dir is required so it's a rename, not a cross-filesystem copy.

When adding a new state file, copy this template. Don't `open(path, "w")` directly — one crash and the file is corrupt.

## Type hints discipline

Python 3.10+ syntax everywhere:

```python
def foo(x: int, y: str | None = None) -> dict[str, list[int]]:
    ...
```

Don't import `Optional`, `List`, `Dict` from `typing` — modern syntax is preferred for consistency. `from __future__ import annotations` is unnecessary; runtime is 3.10+.

Type the **public API** of each `core/` module (functions used from outside). Helper functions can rely on inference. Don't over-type:

```python
# ✅ Public API typed
def compute_hit_stats(closed_trades: list[dict], cash_movements: list[dict] | None = None) -> dict | None:
    ...

# ✅ Helper — inferred is fine
def _eff_pct(t):
    return effective_pnl_pct(t, movements) if movements else float(t.get("pnl_pct") or 0)
```

## Adding a new gate

The gate stack is in `core/analyzer.py`, starting around line 1080 with `if entry_recommendation:` blocks. Order matters — see [CLAUDE.md "Execution-quality gates"](../CLAUDE.md) for the canonical sequence.

Template for a new gate:

```python
if entry_recommendation:
    # Why this gate exists, in one sentence — past incident or guide section.
    _t = (entry_recommendation.get("ticker") or "?").upper()
    _value = ... # compute the thing
    _threshold = config.MY_NEW_THRESHOLD
    if _value < _threshold:   # or whatever the failure condition is
        logger.warning(
            "Entry BLOCKED by my_gate: %s value=%.2f < %.2f",
            _t, _value, _threshold,
        )
        log_gate(_t, "my_gate", True,
                 f"value {_value:.2f} < {_threshold}",
                 {"value": round(_value, 2), "threshold": _threshold})
        entry_recommendation = None
```

Rules:
- **Each gate**: condition check → if blocked, `log_gate(...)` + `entry_recommendation = None`
- **If passed**: store metadata on `entry_recommendation` so downstream visibility (telegram alert, dashboard).
- **Cache stability**: any new context added to `analysis_request` must be cache-stable — no per-call timestamps, no random IDs.
- **Mistake-class mapping**: if the gate corresponds to a user-actionable mistake, add a tag to `config.MISTAKE_TAGS` and update `_MISTAKE_CLASS_MAP` in `telegram_listener.py`.
- **Document**: append to `CLAUDE.md` "Execution-quality gates" list with rationale.
- **Smoke test**: build a tiny scenario that should pass and one that should block, run via inline `python -c`.

## Adding a new Telegram command

Template in `telegram_listener.py`:

```python
@telegram_handler
async def myfeature_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    # 1. Parse args
    args = ctx.args or []
    # ... validate ...

    # 2. Mutate under lock
    with portfolio_lock:
        portfolio = load_portfolio()
        # mutate portfolio
        save_portfolio(portfolio)

    # 3. Respond
    await update.message.reply_text(
        f"✅ Done: ...",
        parse_mode="Markdown",
    )
```

Then register it in `start_listener_thread`:

```python
app.add_handler(CommandHandler("myfeature", myfeature_handler))
```

Update the `help_handler` so `/help` lists it. If the command is destructive, surface a confirmation step or accept a magic confirmation token.

## Adding a new Anthropic tool

1. **Schema** in `core/prompts.py` — define `MY_NEW_TOOL` with name, description, `input_schema`. Append to the relevant `tools=[…]` list at the call site.
2. **Handler** in `core/analyzer.py` after the existing tool-extraction switch (around line 888):
   ```python
   elif name == "my_new_tool":
       my_new_input = block.input or {}
   ```
3. **Persist** via the unified `pending_recommendations` block under lock, with a unique `kind` field.
4. **Two-turn fallback** (`tool_result_content`): when stop_reason is `tool_use` without text and we want a summary, the tool needs a `result_text` line in the if/elif chain.
5. **max_tokens budget**: Tools that emit recommend_entry-class JSON need ≥800 output tokens. Bump `max_tokens` per mode if the tool is added to that mode.

## Time / date handling

- **Persisted**: ISO strings — `"%Y-%m-%d"`, `"%Y-%m-%d %H:%M"`, or `"%Y-%m-%d %H:%M:%S"`. Pick the precision that matches the use case.
- **In-memory**: parse via `datetime.strptime` with the explicit format. Don't rely on `dateutil.parser` — slower and tolerates ambiguity.
- **Relative dates**: convert to absolute on entry. Never persist `"yesterday"` or `"in 3 days"`. The bot stores the absolute date so memory remains interpretable later.
- **Time zone**: assumed CET/CEST (Europe/Berlin). Single user, single zone. Don't introduce `pytz` / `zoneinfo` until we go multi-zone (we won't).
- **Date roll-over**: counters reset at the **first call after midnight**, not at midnight tick. `core.api_usage.get_usage_data()` returns a fresh dict if `data["date"] != str(date.today())`. Same pattern in any per-day counter you add.

## yfinance gotchas

- **404s on commodities / ETFs** without fundamentals are expected. Suppressed in `main.py` `_YFinanceNoiseFilter` for `"possibly delisted"` and `"No fundamentals data found"`.
- **`vol_ratio == 0.0` is "no data"**, not "no volume". yfinance's daily-bar volume isn't aggregated until ~5 minutes after market open. The liquidity gate in `core/analyzer.py` treats `0 < vr < threshold` as a real fail, but `vr == 0` as no-data and keeps the ticker. Don't change this without a strong reason.
- **Cache TTL**: `core.market_data.get_market_data` caches for 60s within a single tick. Repeated calls in the same loop iteration are dedup'd. Reset only with a long sleep or a different tick.
- **`possibly delisted` warning**: transient Yahoo flake, not a real delist. Filtered out — but if a ticker permanently 404s, it'll still return `{"error": ...}` and downstream gates will skip it.
- **Daily-bar timing**: prev_close updates at ~07:00 CET on trading days. Before that, `change_pct` may use yesterday's prev_close → looks like a flat day until the daily bar rolls.

## LS-TC live-feed

Used in `core/livefeed.py` for real-time scrape-based quotes (heartbeat). Best practices:

- **Strict 3-second timeout**. Fail-soft — return `None`, fall back to yfinance on the consumer side.
- **Don't run inside the lock**. Scrape first, then acquire the lock to merge into `portfolio["heartbeat"]`.
- **Throttled at the heartbeat-write level** to ≤1×/min (since 2026-05-07). Don't call directly inside a 10-second loop.
- **HTTP errors are normal** — site rate-limits, slow responses, layout changes. Log at warning, continue.

## Adding a new market_data field

When extending `core.market_data.get_market_data` with a new field, ripple through:

1. **Producer**: add the field to the dict returned per ticker.
2. **`entry_snapshot`** in `telegram_listener.confirm_handler` — mirror it so `_build_thesis_degradation_lines` can compare entry vs current.
3. **`compute_confluence`** in `core/portfolio.py` — add a check if the field signals setup quality.
4. **`format_hit_stats`** — only if it's a stat (rolling average, calibration). Raw values don't belong here.
5. **Frontend type** in `web/lib/types.ts` `OpenTrade`/`ClosedTrade` if it persists to a trade record.

## Backfill scripts

When the schema migrates: **never edit `portfolio.json` by hand**. Use a small Python script:

```python
./venv/bin/python -c "
from core.portfolio import portfolio_lock, load_portfolio, save_portfolio
with portfolio_lock:
    pf = load_portfolio()
    # mutate pf
    save_portfolio(pf)
"
```

Why: hand-edits skip atomic-write, miss `last_updated` bump, and bypass any future schema validation. The script also serves as documentation of the change in commit history.

Past examples (greppable in git log):
- Dividend backfill with `linked_trade` (2026-05-07)
- Stale correlation matrix clear (2026-05-07)
- MAE/MFE seed at entry (2026-05-07)
- PUM entry-price reconcile after manual fill correction (2026-05-08)

## Smoke test discipline

No pytest. Manual smoke pattern via inline `python -c`:

```bash
./venv/bin/python -c "
import main, telegram_listener
from core import *
from core.portfolio import compute_hit_stats
# Set up scenario, call the function, assert
print('OK')
"
```

Always smoke after touching:
- The gate stack (build a passing rec + a blocked rec, verify both)
- Watch-level state machine (expired, invalidated, hit, dedup TTL)
- SL/TP loop (regular hit, partial-TP, BE-shift, trailing activation)
- Lock-holding code paths (race-prone changes — at minimum import the modules)

Quickest sanity check: `./venv/bin/python -c "import main, telegram_listener; from core import *; print('OK')"` — catches import errors, syntax errors, circular imports.

## Common pitfalls

- **Reading `portfolio.json` outside the lock for write paths** — race window between read and write lets the other thread land its own write. Always acquire for read-modify-write sequences.
- **Forgetting `force=True` for high-priority modes** when calling `analyze_portfolio` programmatically — the regular cooldown will silently block. `force=True` uses the forced-cooldown (15min) instead of regular (45min). `bypass_cooldown=True` skips even the forced cooldown.
- **Mutating `_pf_snapshot` from analyzer** — it's a snapshot, downstream gates reuse it. If a gate needs to mutate (rare, but kelly-clamp does), do it on `entry_recommendation` (the dict that gets persisted), not on the snapshot.
- **Cache-busting Anthropic prompt** by including a timestamp inside the system prompt or before the cache marker. Anything that changes per-call must be in the user message, not the cached system block.
- **`_mark_events_triggered` before analyzer attempt** — fixed via TTL 2026-05-07, but the principle remains: if you add a new dedup key, persist with a TTL so a downstream failure doesn't permanently block retries.
- **Updating `entry_price` without `size_eur`** (or vice versa) — they must stay consistent (`size_eur ≈ entry_price × shares`). The dashboard recomputes for display, but stat aggregations may use either.
- **Skipping `last_updated`** — it's set by `save_portfolio` automatically. Don't write it manually unless you're bypassing the helper (you shouldn't).
