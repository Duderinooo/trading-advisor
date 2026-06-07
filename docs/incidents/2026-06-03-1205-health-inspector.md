# Health Report 2026-06-03T12:04:48+02:00

## Overall: 🟡 degraded
Watch-level write failed (0 levels persisted) + red-team reviewer timed out on the only entry generated today (DBK.DE).

---

## Pipeline freshness
Heartbeat age 21s, market hours, 6 API calls. All four modes ran (morning 08:04, xetra-opening 09:10, 3 events). Cadence clean.

---

## LLM channel
Two issues, same transaction (morning, ~08:06):

1. **Red-team timeout** — `claude CLI timed out after 90s (mode=red_team) — skipping critique` (08:06:06). DBK.DE entry recommendation was not adversarially reviewed before being persisted and sent. One-off or recurring? Check `agent_runs.db` for red_team timeout history.

2. **Watch-level zero-return** — `ERROR core.llm.handlers.persist: MORNING WATCHLEVEL FAIL: Sonnet returned 0 levels (existing=3). Prompt requires ≥3.` (08:06:07). Existing 3 levels were preserved by the guard, so watch state is not corrupted — but Sonnet's watch-level output was silently dropped. Possible cause: `out_tok=12803/3500` in the morning trace shows output token budget was exceeded → watch-level JSON truncated. Token budget `3500` is far below actual output.

Event/opening traces all show `tool_called=False` — Haiku passed on IFX.DE +9.5% and 3OIL.MI +11.1% / 3OIS.MI -10.2%. That's a judgment call, not an error.

---

## State integrity
Both DBs: `ok`. No suspicious kv_state entries. Brain traces: all 4 modes present, no warnings.

---

## Open-position aging
`open_trades=0` but log shows `paper: opened DBK.DE 3×€26.82 fee=€1.00 cash_now=€918.54` (08:06:07). Paper handler ran but open_trades count is zero — either paper trades are deliberately excluded from the `open_trades` snapshot passed here, or the trade was not persisted to portfolio. Verify: `sqlite3 state/bot.db "SELECT * FROM pending_recommendations WHERE ticker='DBK.DE'"` and check `portfolio.json` open_trades.

---

## Gate behavior
- `gate_weekly_trend` FNR = **1.0** (N=1 total block, would have won). Below tuning threshold (need ≥20 per CLAUDE.md), informational only — but note the trend.
- `gate_no_entry_zone` FNR = 0.0 (N=73). Working correctly.
- `gate_red_team` FNR = 0.0 (N=1, 1 ambiguous).

---

## Recommended actions

**P1 — Watch-level token budget** (evidence: `out_tok=12803/3500` + ERROR log):
Morning Sonnet output cap `3500` is too tight — actual morning output is 12k+ tokens. The watch-level JSON at end of response is being truncated. Fix: raise `max_tokens` for morning mode in `core/llm/runner.py` or wherever morning's token budget is set. Check current value, set to ≥15000.

**P2 — Red-team timeout** (evidence: `Red-team CLI failed ... timed out after 90s`):
Run `sqlite3 state/agent_runs.db "SELECT ts, error FROM agent_runs WHERE agent LIKE '%red_team%' ORDER BY ts DESC LIMIT 10"` to determine if this is recurring or one-off. If recurring, the 90s hard timeout may be too tight for Sonnet — raise to 120s or add one retry.

**P3 — DBK.DE open-trade discrepancy** (evidence: paper_open log + open_trades=0):
Confirm `portfolio.json` has `DBK.DE` in `open_trades` and that cash reflects the €81.46 deduction (918.54 from 1000.00). If missing, the position exists in the user's mind (Telegram msg_id=392 was sent) but not in bot state → SL/TP loop won't monitor it.