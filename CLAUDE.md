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
4. Extended-UP-Day gate (block when `change_pct > 1.5 × atr14_pct` — chase-protection enforcing manifest HARD-BLOCK #2; asymmetric, only blocks UP-extended days, DOWN-extended remains a potential swing-entry candidate).
5. SL-distance sanity (`MIN_SL_DISTANCE_ATR`×ATR ≤ dist ≤ `MAX_SL_DISTANCE_ATR`×ATR). Too-tight → SL **clamped** wider to the floor (modifier, not blocker — the edge gate below re-validates on the clamped SL). Too-wide → hard reject (clamping tighter would stop before Claude's thesis-invalidation level).
6. Edge gate (`edge_ok`, with Brier-haircut)
7. Sector cluster cap (`MAX_POSITIONS_PER_SECTOR`)
8. VIX size-dampening (modifier, not blocker)
9. Weekly-trend (no LONG vs `wk_trend=DOWN`; override: `reversal_oversold` with RSI<30 + Selling-Exhaustion)
10. Earnings hard-block (T-`EARNINGS_ENTRY_BLOCK_DAYS` to T+0; override: `setup_type=earnings_drift`)
11. Relative-Strength gate (`rs_20d_vs_index_pct ≥ MIN_RS_20D_VS_INDEX_PCT`; override: mean_reversion / reversal_oversold / gap_fill / pre_breakout_squeeze)
12. Volume-Confirm for `setup_type=breakout_resistance` (vol_ratio ≥ `MIN_BREAKOUT_VOLUME_RATIO`)
13. Confluence-Score gate (deterministic 0-10 score ≥ `MIN_CONFLUENCE_SCORE`; relaxed by 2 for mean-reversion family + pre_breakout_squeeze)
14. Correlation gate (≥`MAX_CORRELATED_HOLDINGS+1` holdings with corr ≥ `MAX_CORRELATION` over `CORRELATION_LOOKBACK_DAYS`)
15. DD-soft scaling (modifier: size *= 0.5 between SOFT and HALT thresholds)
16. Auto-split TP at 1R (modifier: single-TP recs get 1R-TP1 prepended for partial scale-out)
17. Whole-share gate (after all size-modifiers: `int(size_eur / entry_price) ≥ 1` — TR-SL läuft nur auf ganzen Stücken; Bruchstück-Position = SL-unmöglich = Verstoß gegen Full-Trust-Invariant)
18. Fee gate (Brutto-Gewinn @ TP1 in €: `(TP1 − entry) × whole_shares ≥ 2 × FIXED_FEE_EUR_PER_SIDE + MIN_NET_PROFIT_EUR` — sonst Trade nach €1+€1 TR-Order-Fees Null-Summe)

Liquidity gate runs earlier, before data even reaches Claude: tickers with `volume_ratio < MIN_VOLUME_RATIO`, `spread_pct > MAX_SPREAD_PERCENT`, or `price > total_capital × MAX_POSITION_SIZE_PERCENT/100 × WHOLE_SHARE_PRICE_BUFFER` (Whole-Share-Pre-Filter, helper `core.portfolio.max_affordable_share_price_eur`) are dropped from `market_data` — open trades + bestehende watch_levels werden geschützt (Exit-/Trigger-Sichtbarkeit). Stage-2-Filter im `set_watch_levels`-Merge verhindert, dass das Protected-Set sich neu mit teuren Tickers füllt.

Slippage gate runs on `/confirm @price`: if `|filled − rec|/rec > MAX_ENTRY_SLIPPAGE_PERCENT`, confirm is rejected and user must re-quote.

VWAP-anomaly gate runs in `core.events.detect_events`: watch-level hits with `|vwap_dev_atr| ≥ 3.0` are dropped (no Claude call). Between 2.0 and 3.0, the event is tagged with a flash-spike warning.

Watch-level proximity has two modes (`core.events.detect_events`): **line-mode** (default) fires on price within ±`BREAKOUT_TRIGGER_PERCENT` of `trigger_price` with all the line-confirm gates active (direction-aware-proximity, `confirm_close_above`, support/resistance buffer). **Zone-mode** (when `zone_low` + `zone_high` are both set on the watch-level) fires on `zone_low ≤ price ≤ zone_high` and **skips** the line-confirm gates — being inside the zone IS the trigger. Volume + VWAP-anomaly checks still apply in both modes. Zone-mode is for accumulation / reversal setups where the entry zone is a band, not a precise line (manifest point 4).

Snapshot field `higher_lows_5d` (`core.market_data._fetch_ticker`): 0-4 consecutive higher-lows over the last 5 daily bars. Base-formation signal for the swing-low family — Sonnet uses it as a positive confirm for `accumulation_zone` / `support_bounce` / `pre_breakout_squeeze` setups.

Snapshot fields `base_quality_score` (0-10) and `base_quality_items` (`core.portfolio.compute_base_quality`, called by `_fetch_ticker`): structural-repair score per the 2026-05-20 manifest point 8. Weighted: selling_exhaustion / atr_contraction / failed_breakdown_reclaim each +2, higher_lows / strong_higher_lows / tight_close / in_base_zone each +1, capped at 10. Primary quality signal for swing-low setups — Sonnet treats it as more important than `confluence_score` (which is momentum/trend-leaning) when picking the Swing-Low family.

## Trade-state automation (events.py SL/TP loop)

- **Stale-Thesis Alert** (`main._check_stale_theses`): position held > per-rec `hold_days_max` → `🕒 STALE THESIS` Telegram, **alert-only, no auto-close**. Once per day. Mechanical Time-Stop was removed 2026-05-04 — Claude sees `entry_date` in open_trades dump and `recommend_exit` is now allowed for "Stagnation + Thesis-Decay-Signal" (entry_snapshot vs current).
- **Exit-Reminder + Auto-Drop** (`main._check_exit_reminders`): pending exit-recs nudgen User. State-Machine pro Rec: Reminder bei Urgency-Threshold → `EXIT_AUTO_DROP_GAP_MIN` Pause → Auto-Drop. Bei Drop wird `trade.exit_dropped_at` gesetzt → `EXIT_REC_COOLDOWN_MIN_AFTER_DROP` (4h) lang werden neue Exit-Recs für diesen Ticker in `core.analyzer` suppressed UND Event-Detection in `core.events` überspringt diese Tickers ganz (kein Haiku-Call → spart €) (verhindert Spiral-Loop). **Außerdem:** wenn schon ein pending Exit für Ticker existiert, werden neue Exit-Recs von Claude verworfen statt ersetzt — Timer wird nicht resettet.
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
- **Token-budget the prompts.** Any new context added to `core/llm/prompt/builder.build_user_message` must be cache-stable (don't embed timestamps or random IDs that bust the cache).

## Tuning rules (no tuning on N=1)

Several constants in `config/` were retuned across the past months in response
to specific incidents. The temptation is to react to every loss / missed-winner
with a threshold tweak — that path leads to event-driven overfitting.

**Rule:** Threshold tuning is allowed only when:

1. **Sample-size sufficient.** ≥`MIN_SAMPLE_SIZE_FOR_TUNING = 20` outcome
   observations (in `config/risk.py`). Single-trade anecdotes do not qualify.
2. **Data-driven evidence.** The change must reference a metric source:
   - `compute_hit_stats(portfolio)` (per-setup_type expectancy)
   - `core.llm.telemetry.outcomes.gate_false_negative_rates()` (per-gate FNR)
   - `analytics/setup_expectancy_history.jsonl` (rolling expectancy)
3. **Postmortem written.** Drop a `research/YYYY-MM-DD-<topic>.md` documenting
   symptom / root cause / fix / constant changed / lessons. One-line pointer
   stays next to the changed constant; full context lives in `research/`.
4. **Inline pointer required.** Every retuned constant must have a comment
   `# YYYY-MM-DD: see research/<topic>.md` directly above. No exceptions —
   future-you should never have to git-blame to find the rationale.

**What does NOT qualify as tuning evidence:**

- "This one PUM.DE trade lost" — N=1
- "Sonnet suggested this" — LLM intuition, not data
- "Felt too strict" — pure vibe
- "Generic best-practice from US-equity literature" — venue-specific (XETRA
  mid-caps need different floors than US large-caps — see
  `research/2026-05-07-breakout-volume-floor.md`)

**Gate-block counterfactuals.** Every entry blocked by a gate is automatically
recorded by `core.llm.telemetry.outcomes.record_blocked_entry`. Next trading
day's daily-OHLC is pulled by `compute_pending_outcomes` (runs in
`services/summary.run_eod_summary`) and resolves whether the blocked trade
would have hit TP1 before SL. `gate_false_negative_rates()` aggregates per-gate
false-negative-rate (blocked-but-would-have-won / total-blocks). This is the
primary tuning-evidence source for "is this gate too strict?"

## Claude models in use

- `CLAUDE_MODEL_MORNING = "claude-sonnet-4-6"` — once per trading day, senior reasoning
- `CLAUDE_MODEL_EVENT = "claude-haiku-4-5"` — all other modes (opening, event, standard)

Do **not** call Opus from this bot — per-call cost doesn't justify it at €1k capital.

## Files not to touch without reason

- `portfolio.json` — mutate only via `save_portfolio` under the lock
- `entities.json`, `.palace/` — MemPalace internals
- `run.sh`, `venv/` — deployment-specific

## Test surface

`tests/` — stdlib `unittest` suite, pure-logic only (no network, no `portfolio.json` mutation). Run from the repo root:
```
./venv/bin/python -m unittest discover -s tests -v
```
Smoke-check imports separately:
```
./venv/bin/python -c "import main, telegram_listener; from core import *; print('OK')"
```

Scope is deliberately limited to pure functions in `core.portfolio` / `core.events` / `core.livefeed` / `macro` — position sizing, risk/edge gates, confluence, correlation, hit-stats, keyword/headline matching, TP/trailing-stop transitions. I/O paths (`load/save_portfolio`, Claude calls, Telegram handlers, `market_data` fetch, `main.py` loop) are **not** covered — testing them needs heavy mocking and the tests turn brittle.

Rules for new tests:
- Reference `config.*` constants, never hard-code gate thresholds — tests must survive config tuning.
- Adding a pure function → add its tests to the matching `tests/test_*.py`.
- Touching an I/O function is still verified by the import smoke-check + a small inline assertion block, not the suite.
