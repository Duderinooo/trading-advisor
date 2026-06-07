# Health Report 2026-06-04T00:05:48 CET

## Overall: 🟡 degraded
One LLM event-mode call failed via `error_max_structured_output_retries` (Ukraine/oil news at 21:27), causing a missed news-cycle analysis. Two bot restarts in 26 min earlier in the evening. Everything else nominal.

---

## Process hygiene
Clean tree: `run.sh` (6422) → `caffeinate -i` (6423) → `main.py` (6425) → `caffeinate -is -w 6425` (6426). Double caffeinate correct — one holds the shell, one watches the Python PID. No orphans.

At 21:35:32 a restart attempt was rejected: `sibling main.py instances detected: [6356]`. Stale PID from the 21:09 shutdown. Auto-resolved; current instance clean since 21:35:43 (uptime 2h30m).

---

## Pipeline freshness
Heartbeat age 6058s (~1.7h) at 00:05 CET. Last tick 22:24:50 — ~14 min after EOD summary (22:10). Outside market hours. No cadence gap concern.

---

## LLM channel
**Failed call at 21:27** — `call_claude_agent` mode=event model=haiku, `exit_code=1`:

```
error_max_structured_output_retries — num_turns=8, stop_reason=tool_use, cost=$0.0714
cache_read_input_tokens=152,618  cache_creation=31,595
```

Haiku burned 8 turns on 184k-token context for Ukraine geo-news → 3OIL.MI / 3OIS.MI and never produced valid structured output. News cycle dropped. Cost: $0.071 — 7× expected Haiku-event budget.

---

## Data quality
`BlockingIOError: [Errno 35] Resource temporarily unavailable` in subprocess fork — appears in log around 21:09 shutdown sequence. Not in current process stack; pre-dates clean restart.

---

## Open-position aging
DBK.DE, entry 2026-06-03 08:06 (~16h). No stale-thesis concern — same trading day.

---

## Gate behavior
`gate_weekly_trend`: FNR=1.0 (total=1, would_win=1). N=1 — below the N≥20 tuning threshold, no action warranted. Flag for accumulation. If this reaches N≥5 with consistent wins, worth a postmortem.

`gate_no_entry_zone`: 74 blocks, 0 wins — working correctly.

---

## Anomalies
`bot.err` full of `MallocStackLogging: can't turn off malloc stack logging` across dozens of Python subprocesses. Cosmetic macOS-python3.14 noise — each `claude` CLI spawn inherits the env. Not actionable but confirms high subprocess churn (each Claude call = new process).

---

## Recommended actions

1. **Investigate `error_max_structured_output_retries`** — check `agents/_lib/runner.py` and the haiku event prompt for why 184k-token context causes tool-call failure after 8 attempts. Consider adding a context-size guard: if `cache_read_tokens > 100k`, truncate older context sections before the Claude call. File: `bot/agents/_lib/runner.py` + `bot/core/llm/prompt/builder.py`.

2. **Track `gate_weekly_trend` FNR** — note the N=1/FNR=1.0 entry today. No tuning action yet, but document the ticker/date so you have a paper trail when N grows.

3. **Suppress `MallocStackLogging` stderr noise** — add `env={**os.environ, "MallocStackLogging": "0"}` (or unset the key) to subprocess calls in `runner.py`. Not functional but makes `bot.err` readable for real signals.