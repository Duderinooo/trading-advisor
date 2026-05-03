# Session Handoff — 2026-05-03

## Repo moved
Working tree now at `/Users/malteollmann/Private Repos/trading-advisor/` (note space in path, quote always). Old path `/Users/malteollmann/trading-advisor/` only has `portfolio.json` + `web/` leftovers.

## Bot is currently NOT running
Killed PID 86828 because it was still loaded from the old path. **launchd plist `/Users/malteollmann/Library/LaunchAgents/com.trading.advisor.plist` still points to `/Users/malteollmann/trading-advisor`** — must be updated before respawn.

Two paths to fix:
- **Option A**: edit the plist `WorkingDirectory` + the `ProgramArguments` python target to the new path, then `launchctl unload && launchctl load` to refresh.
- **Option B**: move repo back to `/Users/malteollmann/trading-advisor` (keep launchd as-is). Simpler if no good reason to live under `Private Repos`.

Verify after: `ps aux | grep python.*main.py` shows process under new path.

## What was done this session

### Bundle A — `feat: 6f54b9d`
Pre-mortem accuracy + hold-time + watch-near-trigger.

- `web/lib/compute.ts` — `preMortemAccuracy()` mirrors Python `fail_mode_to_class` mapping; `holdTimeStats()` actual vs hold_days_max.
- `web/components/Stats.tsx` — two new blocks at bottom (Pre-Mortem-Accuracy, Hold-Time) wired via Dashboard.
- `core/events.py` `detect_events` — heads-up alert when long-watch sits 0.5–1.5% from trigger; `📡 NEAR-TRIGGER` Telegram once per watch per day, dedup via `portfolio.watch_heads_alerted`.

### Bundle B — `feat: 2a80cb6`
DD/ATH alerts + Risk-of-Ruin Monte Carlo.

- `main.py _check_equity_alerts()` — runs each market-hours tick. New ATH `📈 EQUITY NEW ATH` (only after ≥1 closed trade); DD-cross at 5/10/15/20% `⚠️ DRAWDOWN −X% Cross` once per peak. State in `portfolio.equity_ath` + `portfolio.dd_alerts_triggered`.
- `web/lib/compute.ts` — `monteCarloRuin()` (5000 trials × 100-trade horizon, 5% per-trade equity-risk); `maxLossStreak()`.
- `web/components/RiskOfRuin.tsx` (new) — P(ruin) color-coded, median/worst-5% terminal, current/max loss-streak. Hidden until ≥10 closed trades.

### Bundle C — `feat: e1d3179`
MAE/MFE per-trade tracking.

- `main.py` heartbeat — accumulates `mae`/`mfe` per open trade each ~10s using existing live_quotes scrape, no extra HTTP cost.
- `telegram_listener.py confirm_handler` — seeds `mae=mfe=entry` on /confirm. All close paths (`/close` manual + `_close_trade`/`_close_partial` in `core/events.py`) carry MAE/MFE via dict-spread.
- `web/lib/compute.ts maeMfeAnalysis()` — normalizes in R-multiples (R = entry − SL distance).
- `web/components/MaeMfe.tsx` (new) — 4-stat header (winner/loser avg MAE/MFE) + last-12 closed-trade table.

## What's left

### Verification (do once bot respawn pointed at new repo)
1. `./venv/bin/python -c "import main, telegram_listener; from core import *; print('OK')"` — should print `OK`.
2. Telegram `/morning` → reply shows watchlevels; `bot.log` has `MORNING TRACE: tool_called=True`.
3. Open Dashboard `/`:
   - **Stats Card** has Pre-Mortem-Accuracy + Hold-Time blocks (only render when ≥3/5 closed trades respectively — currently 1 closed → won't show until more data).
   - **Risk-of-Ruin Card** present but stays in "≥10 closed trades nötig" state.
   - **MAE/MFE Card** present, fills as soon as the next trade closes.
4. Drawdown/ATH alerts: only fire when conditions hit; nothing to test now beyond confirming `_check_equity_alerts()` runs without exception (look for `Equity alerts check failed` in `bot.log` — should be absent).

### Suggested next steps (not started, from earlier audit)
- **Bundle D-ish** — auto-disable setup-types with <40% hit-rate after ≥5 trades. Was on the deferred list pending more data.
- **Slippage-Outlier alert** — when `/confirm` slippage > 2× rolling avg.
- **VIX-regime-flip alert** — RISK_OFF transition or VIX>30 cross.
- **Daily-Cap-Reached alert** — when `MAX_ANALYSES_PER_DAY` hit.
- **Pre-Earnings T-2 for watch-levels** (currently only open trades alerted).

### Known untouched bugs / nits
- **MSF.DE / RWE.DE / SIE.DE setup_type=None** in existing trades (truncated tool_use pre-fix). Backfill not possible (Sonnet intent lost). New trades won't have this issue.
- **3OIL.MI live-quote** worked at audit time but ISIN `IE00B7Y34M31` was guessed; verify by checking `portfolio.json.heartbeat.live_quotes["3OIL.MI"]` once a watch fires.
- **Web dashboard `/brain` AnalyzeTraceCard** persists last-trace per mode; opening_trace_xetra/us only fill when those modes actually run today.

## Latest commits (newest first)
```
e1d3179 feat: Bundle C — MAE/MFE per-trade tracking + dashboard insight
2a80cb6 feat: Bundle B — drawdown/ATH alerts + Risk-of-Ruin Monte Carlo
6f54b9d feat: Bundle A — pre-mortem accuracy + hold-time + watch-near-trigger
291c1de fix: 5 news-pipeline bugs from 2026-05-02 audit
4a32ea5 fix: drop incomplete recommend_entry + refresh correlation on /confirm
dfb7ba8 fix: 3 text-parse bugs + bump event/opening max_tokens
```

`origin/main` is N commits behind — not pushed automatically. `git push` when ready.
