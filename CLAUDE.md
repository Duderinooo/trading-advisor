# Trading Advisor — AI Agent Guide

Bot for a single user who executes **every** recommendation 1:1 on Trade Republic (TR). No self-directed trading. Bot = sole filter, so conservative bias is the default everywhere.

## Runtime shape

- `main.py` — event loop (morning prep, opening checks, price + event + news polling every 15 min)
- `telegram_listener.py` — background thread, user commands (`/confirm`, `/close`, `/panic`, …)
- `core/` — pure logic (market data, portfolio I/O, Claude orchestration, event detection)
- `memory.py` — MemPalace wrapper for trade/analysis history (optional; gracefully degrades)
- `portfolio.json` — single source of truth for open/closed trades, cash, watch levels, kill-switch

All writers acquire `core.portfolio.portfolio_lock` (`RLock`). Both the main loop and the Telegram thread mutate state — never skip the lock.

## Critical invariants

1. **Every `/confirm` requires a stop-loss** (`telegram_listener.confirm_handler`). Rec without SL → rejected. Full-trust bias: no SL = no trade.
2. **Risk-halt gates every new entry**: kill-switch, daily loss cap, drawdown cap, portfolio heat, edge (`p·b − (1−p) ≥ MIN_EXPECTED_EDGE`), RISK_OFF + LONG. All live in `core/analyzer.py` before `rec` is persisted.
3. **Position sizing = `min(ATR-risk, fractional-Kelly, hard cap)`** (`core/portfolio.suggest_position_size`). Do not add capacity above `MAX_POSITION_SIZE_PERCENT`.
4. **TR supports Bruchstücke (fractional shares)**: `shares` is `float`, rounded to 4 decimals. Do not cast to `int`.
5. **Actionable-only Telegram**: event/price/news verdicts are sent only if `_is_actionable(analysis)` (prefix ENTRY/EXIT/BUY/SELL/KAUFEN/VERKAUFEN/CLOSE). PASS/HALTEN/HOLD are logged and dropped to avoid noise.
   - **Filter at output, not input.** Do NOT drop news/events before they reach Claude "to save cost" — Claude must see all stock/geo news to decide if an entry exists. A dropped news cycle missed Intel earnings +20% previously. The output-level `_is_actionable` check already suppresses PASS/HALTEN noise.
6. **Kill-switch** blocks new entries and event/news Claude calls, but leaves SL/TP monitoring running. Never let `run_price_check` skip SL checks under the kill-switch — open positions must still auto-exit. `run_morning_prep` and `run_opening_check` also skip silently under kill-switch and mark themselves done so they don't retry until the next day (incident 2026-04-29: 15× retry-spam when kill-switch was off but cache_control bug caused error path).

## Manual override semantics (2026-04-29)

- `analyze_portfolio(force=False, bypass_cooldown=False)`:
  - `force=True` is auto-set for `mode in ("morning","opening","event")` — uses the *forced-call* cooldown (`MIN_MINUTES_BETWEEN_FORCED_ANALYSES = 15`) instead of the regular cooldown. Daily cap still enforced.
  - `bypass_cooldown=True` skips even the forced-cooldown. Only `MAX_ANALYSES_PER_DAY` blocks. Use only for explicit user-triggered runs.
- `run_morning_prep(force=False)`:
  - `force=True` (used by Telegram `/morning` handler) bypasses three things together: `last_morning_prep_date` dedup, `kill_switch_active` skip, *and* the forced-call cooldown (passes `bypass_cooldown=force` into `analyze_portfolio`).
  - Auto-runs from the main loop pass `force=False` and remain strict.
- `run_opening_check(market)` has no `force` flag — auto only, no manual trigger.

The asymmetry is intentional: `/morning` is the recovery lever for a missed or empty morning prep (e.g. cache_control regression, regime-defensiveness false-positive). All other paths must honor cooldowns.

## Execution-quality gates (guide-aligned, order matters)

Located in `core/analyzer.analyze_portfolio`, applied in this order on a `recommend_entry` tool call:

1. `risk_halt_status` (kill-switch, daily loss, drawdown, heat)
2. Regime gate (`RISK_OFF_BLOCKS_LONGS`)
3. No-entry-zone (auction/EOD windows)
4. SL-distance sanity (0.8×ATR ≤ dist ≤ 3.0×ATR)
5. Edge gate (`edge_ok`, with Brier-haircut)
6. Sector cluster cap (`MAX_POSITIONS_PER_SECTOR`)
7. VIX size-dampening (modifier, not blocker)
8. Weekly-trend (no LONG vs `wk_trend=DOWN`)
9. Earnings hard-block (T-`EARNINGS_ENTRY_BLOCK_DAYS` to T+0; override: `setup_type=earnings_drift`)
10. Relative-Strength gate (`rs_20d_vs_index_pct ≥ MIN_RS_20D_VS_INDEX_PCT`; override: mean_reversion / reversal_oversold / gap_fill)
11. Volume-Confirm for `setup_type=breakout_resistance` (vol_ratio ≥ `MIN_BREAKOUT_VOLUME_RATIO`)
12. Confluence-Score gate (deterministic 0-10 score ≥ `MIN_CONFLUENCE_SCORE`; relaxed by 2 for mean-reversion family)
13. Correlation gate (≥`MAX_CORRELATED_HOLDINGS+1` holdings with corr ≥ `MAX_CORRELATION` over `CORRELATION_LOOKBACK_DAYS`)
14. DD-soft scaling (modifier: size *= 0.5 between SOFT and HALT thresholds)
15. Auto-split TP at 1R (modifier: single-TP recs get 1R-TP1 prepended for partial scale-out)
16. Whole-share gate (after all size-modifiers: `int(size_eur / entry_price) ≥ 1` — TR-SL läuft nur auf ganzen Stücken; Bruchstück-Position = SL-unmöglich = Verstoß gegen Full-Trust-Invariant)
17. Fee gate (Brutto-Gewinn @ TP1 in €: `(TP1 − entry) × whole_shares ≥ 2 × FIXED_FEE_EUR_PER_SIDE + MIN_NET_PROFIT_EUR` — sonst Trade nach €1+€1 TR-Order-Fees Null-Summe)

Liquidity gate runs earlier, before data even reaches Claude: tickers with `volume_ratio < MIN_VOLUME_RATIO`, `spread_pct > MAX_SPREAD_PERCENT`, or `price > total_capital × MAX_POSITION_SIZE_PERCENT/100 × WHOLE_SHARE_PRICE_BUFFER` (Whole-Share-Pre-Filter, helper `core.portfolio.max_affordable_share_price_eur`) are dropped from `market_data` — open trades + bestehende watch_levels werden geschützt (Exit-/Trigger-Sichtbarkeit). Stage-2-Filter im `set_watch_levels`-Merge verhindert, dass das Protected-Set sich neu mit teuren Tickers füllt.

Slippage gate runs on `/confirm @price`: if `|filled − rec|/rec > MAX_ENTRY_SLIPPAGE_PERCENT`, confirm is rejected and user must re-quote.

VWAP-anomaly gate runs in `core.events.detect_events`: watch-level hits with `|vwap_dev_atr| ≥ 3.0` are dropped (no Claude call). Between 2.0 and 3.0, the event is tagged with a flash-spike warning.

## Trade-state automation (events.py SL/TP loop)

- **Stale-Thesis Alert** (`main._check_stale_theses`): position held > per-rec `hold_days_max` → `🕒 STALE THESIS` Telegram, **alert-only, no auto-close**. Once per day. Mechanical Time-Stop was removed 2026-05-04 — Claude sees `entry_date` in open_trades dump and `recommend_exit` is now allowed for "Stagnation + Thesis-Decay-Signal" (entry_snapshot vs current).
- **Partial TP**: TP1 hit on a multi-TP rec sells `PARTIAL_TP_FRACTION × shares` (default 50%), records the partial as a separate `closed_trades` entry with `partial=True`, then moves SL to break-even and activates 1.5×ATR trailing on remainder. Final TP closes full remainder.
- **Brier scoring on partials**: scored only on `partial_seq=1` (first close); later partials carry no `brier`/`outcome` to avoid double-counting.

## Learning loop

- `/close TICKER @price #tag` — `#tag` ∈ `config.MISTAKE_TAGS` (8 tags → 4 guide classes: prediction / timing / execution / external). Stored on the closed trade as `mistake_tag` + `mistake_class`. Losses without a tag → `mistake_class = "untagged"` and user is reminded.
- `compute_hit_stats` produces `class_suggestion` when any tagged class reaches ≥40% of the last ≥5 losses. The suggestion is a concrete parameter change (tighten spread, raise conviction gate, etc.), logged and injected into the morning prompt.
- Brier-based `calibration.haircut` corrects Claude's `p_win` estimate when rolling bias ≥5%.
- Every analysis (all modes, all tickers) receives the last-20-loss class distribution via `mistake_summary`.

## Adding features — rules

- **No new abstraction without three concrete call sites.** This is a single-user bot; generality pays no rent here.
- **Don't add fallback paths that can't happen.** E.g. `load_portfolio` always returns a dict — no `None` guards.
- **Config changes must come with a one-line comment** explaining the trigger (past incident, guide section, etc.). Future-you needs the *why*.
- **Never bypass `portfolio_lock`** on write paths. Reads are fine without, writes are not.
- **Prefer extending existing files.** `core/` submodules already slice the surface; new files must justify themselves.
- **Token-budget the prompts.** Any new context added to `core/analyzer.analysis_request` must be cache-stable (don't embed timestamps or random IDs that bust the cache).

## Claude models in use

- `CLAUDE_MODEL_MORNING = "claude-sonnet-4-6"` — once per trading day, senior reasoning
- `CLAUDE_MODEL_EVENT = "claude-haiku-4-5"` — all other modes (opening, event, standard)

Do **not** call Opus from this bot — per-call cost doesn't justify it at €1k capital.

## Files not to touch without reason

- `portfolio.json` — mutate only via `save_portfolio` under the lock
- `entities.json`, `.palace/` — MemPalace internals
- `run.sh`, `venv/` — deployment-specific

## Test surface

No test suite. Verify changes with:
```
./venv/bin/python -c "import main, telegram_listener; from core import *; print('OK')"
```
For logic changes, write a small inline assertion block (see how `compute_hit_stats` was smoke-tested) rather than introducing pytest.
