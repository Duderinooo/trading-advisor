# Health Report 2026-05-29T01:14:03+02:00

## Overall: 🟡 degraded
Rate-limit hit killed 2 event calls at 17:28 CET; heartbeat stale 2h50min with no loop activity logged since 22:24.

---

## Process hygiene
Clean tree. `run.sh` (59758) → `caffeinate` (59760) → `main.py` (59762) → `caffeinate -is -w 59762` (59763). No orphans. Uptime ~30h.

---

## Pipeline freshness
Heartbeat last tick `22:24:32`, age **10,170s = 2h49m**. No log entries after 22:10 EOD. Process alive (ps confirmed), but zero evidence of loop ticks from 22:24→01:14. Either heartbeat only written on active cycles, or main loop is stalled overnight. Off-market hours so not critical, but 3-hour silence without a single heartbeat write is not proven normal.

---

## LLM channel
Two consecutive `AgentRunError` at **17:28:39** and **17:28:40**:
```
claude CLI exit=1: "You've hit your limit · resets 5:30pm (Europe/Berlin)"
```
Both triggered by Geo-NEWS → `3OIL.MI` Iran/US hostilities event. Two news cycles fully missed during active market hours. Recovery confirmed at 17:43 (next event call succeeded). No errors in agent_runs recent-20. `api_calls_today = 6` — low count, consistent with rate-limit having throttled the day.

---

## State integrity
`bot.db` + `agent_runs.db` both `ok`. Brain traces present for all 4 modes (morning, opening_xetra, opening_us, event), zero warnings. No suspicious kv_state entries.

---

## Gate behavior
| Gate | N | FNR | Status |
|---|---|---|---|
| `gate_no_entry_zone` | 73 | 0.0 | Clean |
| `gate_weekly_trend` | **1** | **1.0** | N<20, below tuning threshold |
| `gate_red_team` | 1 | 0.0 | N<20, fine |

`gate_weekly_trend` FNR=1.0 is not actionable (N=1 vs `MIN_SAMPLE_SIZE_FOR_TUNING=20`), but worth tracking — one blocked trade would have won.

---

## Anomalies
`bot.err` contains 40 lines of `MallocStackLogging: can't turn off malloc stack logging` from child Python PIDs. These are macOS system messages from claude CLI subprocess spawns, not application errors. Benign but accumulating — consider suppressing in log rotation.

---

## Recommended actions

1. **Verify overnight heartbeat design** (`main.py` loop): check whether `runtime_store.set_heartbeat()` is called every loop iteration or only on active processing. If the latter, consider adding a bare tick every N cycles to confirm liveness. Evidence: 10,170s gap with confirmed live process.

2. **Rate-limit recovery is passive** — 2 Iran-ceasefire event calls dropped at market-open equivalent. Consider adding retry-with-backoff or queuing when `"You've hit your limit"` is detected, rather than full `AgentRunError`. Current behavior silently drops the event. Evidence: `AgentRunError` at 17:28:39 and 17:28:40, no retry seen in log.

3. **Watch `gate_weekly_trend`** — log its blocks to a tally. At N=20 run `gate_false_negative_rates()` to determine if threshold needs revisiting. No action now. Evidence: FNR=1.0 at N=1.

4. **MallocStackLogging stderr noise** — suppress at log-rotation level or redirect claude CLI stderr. Not a bug, but pollutes error-signal channel. Evidence: 40 of 40 bot.err lines are this message.