# Health Report 2026-05-30T20:11:17+02:00

## Overall: 🟡 degraded
Bot process alive but current instance (5h40m running) has made 0 API calls and never updated heartbeat — coincident with Telegram DNS failure in logs.

---

## Pipeline freshness

Heartbeat last_tick = `10:14:31` → **35806s stale (~9.9h)**. Current process PID 75315 started at ~14:30 CET (uptime 5h40m). Delta: process has been running for 5h40m and has **never written a heartbeat tick**. `api_calls_today = 0` confirms no Claude calls from this instance. US market open at 20:11 CET (14:11 ET) — bot should be in active polling.

---

## LLM channel

`api_calls_today = 0`. Last `call_claude_agent` in agent_runs.db = id=208, ts≈14:10 CET — predates current process start. No Haiku event calls since process restart at ~14:30. US opening check + any afternoon event triggers missed entirely.

---

## Process hygiene

Process tree clean: `run.sh` (75312) → `caffeinate -i` (75313) → `main.py` (75315) → `caffeinate -is -w 75315` (75316). No orphans, proper caffeinate wrapper on Python pid.

---

## State integrity

`bot.db` + `agent_runs.db` both `PRAGMA integrity_check = ok`. Brain traces present for all 4 modes (morning, opening_xetra, opening_us, event) — no warnings, but timestamps not surfaced; likely from pre-restart process.

---

## Gate behavior

`gate_weekly_trend`: FNR = **1.0** (1/1 blocked — would have won). N=1, below `MIN_SAMPLE_SIZE_FOR_TUNING=20`, not actionable. Flag for accumulation.

---

## Anomalies

**Telegram DNS failure in bot.log:**
```
httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known
```
Telegram polling failing on DNS resolution. If this network fault also affects `api.anthropic.com`, it explains 0 API calls. The bot is silently swallowing the Anthropic call failures (no error in agent_runs.db, no bot.err trace for Claude) — or calls are simply not being attempted because an upstream health check is blocking the loop.

`market_hours = false` at 20:11 CET while US session is live. Either the bot's `market_hours` flag is XETRA-only, or the main loop is stalled. Either way, US afternoon polling not running.

---

## Recommended actions

1. **Check network now**: `curl -s https://api.telegram.org` and `curl -s https://api.anthropic.com` — confirm whether DNS issue is resolved or ongoing.
2. **Investigate why main loop isn't calling Claude**: grep `bot.log` for lines after 14:30 — `grep "$(date +%Y-%m-%d) 1[456789]\|2[01]:" bot.log` — find if loop is running but skipping, or truly stalled.
3. **Check market_hours logic**: if flag is XETRA-only, US tickers get no afternoon event polling. Verify `main.py` market-hours gate covers US session or that US events fire regardless.
4. **Restart bot if network is now healthy**: 5h40m of zero activity during a live US session is unacceptable even at €1k capital. Kill PID 75312 and re-run `run.sh`.
5. **Gate_weekly_trend FNR=1.0**: note for accumulation — once N≥20, evaluate if weekly-trend DOWN gate is too strict for current watch list.