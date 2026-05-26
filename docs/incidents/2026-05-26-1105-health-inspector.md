# Health Report 2026-05-26T11:04:36+02:00

## Overall: 🟡 degraded
Previous bot instance crashed at 08:00:04 (ULIMIT fd exhaustion); orphan caffeinate process still running. Current instance recovered and is operating cleanly, but root cause (fd leak) unconfirmed as resolved.

---

## Process hygiene
Orphan caffeinate **PID 98366** (PPID 1, PGID 98364, elapsed 11h51m) not part of current bot (PGID 52900). Leaked from a prior session — predates yesterday's 23:13 start. Harmless but accumulates if not cleaned.

---

## Pipeline freshness
Heartbeat age: **1s**. Clean.

Previous instance ran ~3h before crashing at 08:00:04. Restart completed in 3s (08:00:07). Current instance uptime: 3h04m. No missed cadence windows post-restart — morning prep (08:02), opening XETRA (09:10), 3 event calls, 3 bug-watcher runs all executed on schedule.

---

## LLM channel
Morning trace: `out_tok=5664` vs budget `3500` — exceeded by 62%. Not truncated, but token creep risk. All other runs within budget.

Model mix correct: morning=sonnet, all other modes=haiku. 5 API calls today. All agent_runs `exit_code=0`, no errors.

Three event calls returned `tool_called=False` (PASS verdicts) — appropriate given 3OIL.MI spread 3.1–3.9% and 4GLD.DE price >€100 whole-share cap both correctly gate-dropped.

---

## Data quality
**3OIL.MI** extreme volatility: −18.1% at 08:18, +9.1% at 11:03 (US-Iran strikes). Liquidity gate correctly rejected both times (spread 3.1–3.9%). GEO news dedup working — 8+ articles suppressed after initial fire.

---

## State integrity
**Root cause of crash:** `OSError: [Errno 24] Too many open files` hit portfolio.json, `.api_usage.json`, and heartbeat at 08:00:04. Pre-crash indicator visible: 10× `sqlite3.OperationalError: unable to open database file` in `monitoring-agents tick` from 07:58:23–07:59:54 (same fd exhaustion, different surface).

Current instance: no fd errors since 08:00:07. DB integrity: both `bot.db` + `agent_runs.db` = **ok**.

Risk: if the fd leak is still present in current instance (just slower), it will crash again around 11:00–12:00 based on ~3h TTF observed.

---

## Open-position aging
2 positions (BAYN.DE, MBG.DE). Earliest entry 2026-05-21 13:35 — **5 calendar days / ~3 trading days**. Not yet stale.

BAYN.DE watch level `support_bounce @38.00` not confirming across 6 consecutive checks (09:03–11:04). Price drifting lower: 38.16 → 38.09 → 38.14 → 38.05 → 37.90 → 37.83. Needs ≥38.26 for bounce confirm. Thesis weakening — support dissolving below trigger level.

---

## Recommended actions

**Priority 1 — FD leak (crash risk):**
```bash
lsof -p 52903 | wc -l
```
Run now and again in 30 min. If count growing steadily → active leak in current instance, restart before market close. Check `core/db.py:connect` and `portfolio/io.py` for unclosed file handles (previous crash stack points to both).

**Priority 2 — Kill orphan caffeinate:**
```bash
kill 98366
```

**Priority 3 — Morning token budget:**
Morning `out_tok=5664` exceeds `3500` budget by 62%. Identify what expanded (watch levels? more tickers?). If consistent, raise budget or trim context in `core/llm/prompt/builder.build_user_message`.

**Priority 4 — BAYN.DE watch level:**
Price now 37.83, below support @38.00. Monitor next 1–2 price checks — if no reclaim, the support_bounce thesis is likely invalid and the watch level should be reviewed (user decision).

<!-- bug-worker-status: processed ts=2026-05-26 reason=already-fixed-manually -->
