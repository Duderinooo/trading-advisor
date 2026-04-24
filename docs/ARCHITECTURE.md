# Architecture

Module map and data flow. Read alongside `CLAUDE.md` (conventions) and `README.md` (setup).

## Process topology

```
┌────────────────────────────────────────────────────────────┐
│ main.py (foreground)                                        │
│  ├─ signal handlers (SIGINT/SIGTERM → graceful_shutdown)    │
│  ├─ 30s tick loop                                           │
│  │   ├─ is_morning_prep_time()     → run_morning_prep       │
│  │   ├─ is_xetra_open_check_time() → run_opening_check      │
│  │   ├─ is_us_open_check_time()    → run_opening_check      │
│  │   ├─ every 15 min during market hours:                   │
│  │   │   ├─ run_price_check  (SL/TP + price alerts)         │
│  │   │   ├─ run_event_check  (watch-level hits)             │
│  │   │   └─ run_news_check   (geo + stock headlines)        │
│  │   └─ every 15 min Sunday 18-22 CET:                      │
│  │       └─ run_news_check   (weekend geo-news catch-up)    │
│  └─ start_listener_thread() ─── daemon ─┐                   │
└──────────────────────────────────────────┼──────────────────┘
                                           ▼
                                  ┌────────────────────────┐
                                  │ telegram_listener.py   │
                                  │ (asyncio loop, daemon) │
                                  │  /confirm /close /...  │
                                  └────────────────────────┘
```

Both threads mutate `portfolio.json` and therefore acquire `core.portfolio.portfolio_lock` on every load-modify-save sequence.

## Module responsibilities

### `main.py`
Schedules checks, routes Claude output to notifications. Owns the actionable-only filter `_is_actionable`. Must not contain market-data or portfolio logic — those live in `core/`.

### `core/portfolio.py`
- I/O: `load_portfolio`, `save_portfolio` (atomic tmp + rename)
- Lock: `portfolio_lock` (RLock — re-entrant so helpers can nest)
- Sizing: `suggest_position_size`
- Risk halts: `risk_halt_status` (kill-switch + daily-loss + drawdown + heat), `edge_ok`, `kill_switch_active`, `set_kill_switch`
- Stats: `compute_hit_stats` (win-rate, conviction, Brier, class-aware self-calibration), `compute_portfolio_heat`, `compute_sector_exposure`, `compute_equity_stats`

### `core/market_data.py`
- `get_market_data(tickers)` with per-ticker TTL cache
- `_compute_indicators` — MA20/50/200, RSI14, MACD, BB, ATR14 (+%), weekly trend, VWAP, `vwap_dev_atr`
- `get_earnings_warnings` — cached 6h per ticker
- `fetch_news` — yfinance headlines
- `market_regime` — RISK_ON / RISK_OFF / NEUTRAL from SPY vs 200MA plus VIX bands

### `core/events.py`
- `check_news_events` — scans watchlist + commodities for headlines; dedupes by MD5 per day
- `detect_events` — watch-level hit detector; drops extreme VWAP spikes (≥3×ATR); tags flash-spike warnings (≥2×ATR)
- `should_analyze_events` — priority + cooldown gate
- `check_stop_loss_take_profit` — trailing stops, staged TPs, break-even shift after TP1
- `check_price_alerts` — ≥DROP/RISE threshold, pre-filtered to watch-level tickers or ≥STRONG_PRICE_ALERT_PERCENT

### `core/analyzer.py`
Single orchestrator: `analyze_portfolio(mode, event_context, force)`.

Modes: `morning` (Sonnet, full brief), `opening` (Haiku, gap check), `event` (Haiku, action-only), `standard`.

Responsibilities:
1. Pull market + macro + regime + portfolio + history + mistake-class summary
2. Apply liquidity pre-filter to `market_data`
3. Compose prompt with format-enforcer header per mode
4. Call Claude with tool definitions (`set_watch_levels`, `recommend_entry`)
5. Handle 2-turn flow when a tool is called without text
6. Gate any `entry_recommendation` through: risk-halt → regime → edge
7. Persist watch levels + pending rec under the lock; notify via Telegram with `message_id` anchor for reply-based `/confirm`

### `telegram_listener.py`
Commands (all require `_authorized` check against `TELEGRAM_CHAT_ID`):

| Command | Effect |
|---|---|
| `/confirm [shares] [@price]` (reply) | Move pending rec → open_trades; enforces SL + slippage gate |
| `/close TICKER [@price] [#tag]` | Close open trade; tag classifies loss into taxonomy |
| `/cancel` (reply) | Drop a pending rec |
| `/positions` | Print cash + open + pending |
| `/panic [reason]` | Kill-switch ON |
| `/resume` | Kill-switch OFF |
| `/killstatus` | Current kill-switch state |
| `/help`, `/start` | Cheat-sheet |

### `memory.py`
Thin MemPalace wrapper. `MEMPALACE_AVAILABLE = False` if import fails → every helper becomes a no-op. Safe to call from anywhere.

Wings used:
- `trades` (rooms = ticker)
- `analyses` (rooms = mode)
- `patterns` (helper exists, currently unused — earmarked for archetype work)

### `notifier.py`, `macro.py`
Telegram bot API wrapper (send + edit) and macro-event calendar fetcher. Stable boundaries — rarely change.

## Data flow: from watch-level hit to /confirm

```
detect_events()                         [core/events.py]
 ├─ filters excluded + extreme VWAP
 └─ returns [{type: WATCH_LEVEL_HIT, ticker, trigger_price, anomaly, …}]
          │
          ▼
run_event_check()                       [main.py]
 ├─ should_analyze_events()
 └─ analyze_portfolio(mode="event")     [core/analyzer.py]
          │
          ▼
Claude (Haiku)
 ├─ may emit text verdict (ENTRY/EXIT/PASS)
 └─ may call recommend_entry tool with structured rec
          │
          ▼
risk-halt → regime → edge gates         [core/analyzer.py]
          │ pass
          ▼
notify() anchors Telegram message_id on rec
          │
          ▼
portfolio.json (pending_recommendations)
          │
          ▼
User executes on TR, sends `/confirm 3 @172.50` as reply
          │
          ▼
confirm_handler                         [telegram_listener.py]
 ├─ SL mandatory check
 ├─ slippage gate
 └─ append to open_trades, debit cash, log_trade(..., "CONFIRMED")
```

## Learning loop

```
/close NVD.DE @165 #thesis_wrong
          │
          ▼
close_handler tags mistake_class="prediction"
          │
          ▼
portfolio.json (closed_trades[].mistake_class)
          │
          ▼ next morning
compute_hit_stats()
 ├─ rolling 20-loss class distribution
 ├─ if class ≥40%: class_suggestion = "raise conviction gate + p_win +0.05"
 └─ Brier-based haircut on p_win
          │
          ▼
Morning prompt includes:
  - LAST-20 MISTAKES (taxonomy)
  - HIT-RATE with Brier + SELBST-KALIBRIERUNG
          │
          ▼
Claude adapts conviction / p_win for today's recs
```

## Config flags (decision-relevant only)

| Flag | Purpose | Default |
|---|---|---|
| `MAX_RISK_PER_TRADE_PERCENT` | ATR-risk leg of size | 3.0 |
| `MAX_POSITION_SIZE_PERCENT` | Hard size cap | 8.0 |
| `KELLY_FRACTION` | Fractional Kelly scalar | 0.25 |
| `MIN_EXPECTED_EDGE` | `p·b − (1−p)` floor | 0.04 |
| `DAILY_LOSS_HALT_PERCENT` | Day-halt trigger | 5.0 |
| `DRAWDOWN_HALT_PERCENT` | Equity-halt trigger | 8.0 |
| `MAX_PORTFOLIO_HEAT_PERCENT` | Summed open risk cap | 10.0 |
| `MAX_ENTRY_SLIPPAGE_PERCENT` | `/confirm` reject threshold | 2.0 |
| `MIN_VOLUME_RATIO` | Liquidity pre-filter | 0.3 |
| `MAX_SPREAD_PERCENT` | Liquidity pre-filter | 0.75 |
| `RISK_OFF_BLOCKS_LONGS` | Hard-block longs under RISK_OFF | True |
| `MISTAKE_TAGS` | Valid `/close #tags` | see `config.py` |

## Known limitations

- **No broker API** — bot assumes user executes rec 1:1 on TR. Fills and partial executions are only observable through `/confirm @price`.
- **Setup archetype not tracked** — `log_pattern` helper exists but isn't called; per-archetype Brier is therefore not available.
- **Class suggestion is textual**, not auto-applied to `config.py`. User reads and decides.
- **yfinance delayed data** (~15 min). Polling faster than 15 min gains nothing.
