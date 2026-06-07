# Health Report 2026-06-04T06:06:48+02:00

## Overall: 🟡 degraded
One haiku event call failed with `error_max_structured_output_retries` (geo-news 3OIL.MI/3OIS.MI unanalyzed, $0.07 burned). Two restart cycles in 26 min window with a `BlockingIOError` subprocess fault also visible.

---

## Process hygiene
Clean single instance. Hierarchy correct: `run.sh`(6422) → `caffeinate -i`(6423) → `main.py`(6425) → `caffeinate -is -w 6425`(6426). No orphans.

---

## Pipeline freshness
Heartbeat age 27,718s (~7.7h, last tick 22:24:50). Market closed; XETRA opens 09:00 CET. Age is expected — pre-market overnight gap. No anomaly.

---

## LLM channel
**agent_runs id=296 failed**: `error_max_structured_output_retries`, haiku/event mode, 8 turns exhausted, `stop_reason=tool_use`. Triggered by geo-news check for Ukraine/NDR article at 21:27. $0.071 wasted. Root: haiku looped on tool-use output format and never resolved.

Consequence: geo-news on 3OIL.MI and 3OIS.MI (Ukraine war news) was **not analyzed**. No alert sent to user.

---

## State integrity
Both `bot.db` and `agent_runs.db` pass `PRAGMA integrity_check`. All 4 brain traces present (morning, opening_xetra, opening_us, event), no warnings.

---

## Open-position aging
1 position: DBK.DE, entered 2026-06-03 08:06 (~22h ago). Not stale.

---

## Gate behavior
`gate_weekly_trend`: FNR=1.0 (1 block, 1 would-win). **N=1 — below `MIN_SAMPLE_SIZE_FOR_TUNING=20`, no action warranted.** Flag for tracking: next would-win on this gate approaches the threshold.

`gate_no_entry_zone`: 74 blocks, FNR=0.0. Perfectly calibrated.

---

## Anomalies
**Double restart, 21:09–21:35**: Bot shut down at 21:09, restarted 21:11, shut down again 21:35, then at 21:35:32 logged `sibling main.py instances detected: [6356]` — the old instance hadn't exited cleanly yet. Bot started anyway (PID collision resolved). `BlockingIOError: [Errno 35]` in the log tail is a subprocess spawn failure from the restart churn — likely Claude CLI subprocess during the failing event call.

`bot.err` shows only macOS `MallocStackLogging` noise across many PIDs — cosmetic, suppress-safe.

---

## Recommended actions

1. **Investigate haiku structured-output-retries loop** (`agents/_lib/runner.py:207`): `error_max_structured_output_retries` with 8 turns + `stop_reason=tool_use` means haiku couldn't format a `recommend_entry` / `pass_no_action` tool call correctly after the geo-news context. Check if the prompt for event mode recently changed tool schema (`core/llm/prompt/prompts/tools.py`) — a schema mismatch would cause exactly this loop. Add a fallback: after 3 retries inject an explicit system message forcing `pass_no_action`.

2. **Re-run geo-news analysis for 3OIL.MI / 3OIS.MI manually** if the Ukraine news (NDR article 21:27) is still market-relevant at today's open. The analysis was dropped silently.

3. **Investigate double-restart cause**: Check what triggered the 21:09 shutdown — review log lines before the shown tail. If it was a user `/stop` + immediate `/start` that raced with `BlockingIOError`, the subprocess guard in `run.sh` or `main.py` startup may need a small sleep before the sibling-check.

4. **Track `gate_weekly_trend` FNR**: Currently N=1, FNR=1.0. At N=5 with FNR>0.6, escalate to research investigation. No tuning action yet per policy.