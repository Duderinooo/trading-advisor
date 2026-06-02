# Health Report 2026-05-30T08:02:10+02:00

## Overall: 🟡 degraded
Telegram polling dead (DNS failure); heartbeat 9.6h stale while process alive.

## Process hygiene
4 PIDs, all PGID 45982, uptime 16h32m. Process tree clean: run.sh → caffeinate wrapper → Python main.py → `caffeinate -is -w 45985`. No orphans, no duplicates.

## Pipeline freshness
Heartbeat last tick `2026-05-29 22:24:14` — **34 658 s / 9.6h ago**. Process alive, market hours false at 08:02 CET. If heartbeat only writes on active loop ticks, overnight silence is partially expected, but 22:24→08:02 spans US close + full night with zero writes. Suggests main loop is either blocked in Telegram error-retry or heartbeat write is suppressed.

## LLM channel
agent_runs last 7 entries: all `exit_code=0`. morning + opening XETRA/US + event traces present with no warnings. 8 API calls today. Clean.

## Anomalies
**Telegram DNS failure** — bot.log tail entirely consumed by:
```
telegram.error.NetworkError: httpx.ConnectError: [Errno 8] nodename or servname not known
```
DNS cannot resolve Telegram API hostname. Consequence: bot cannot send trade alerts (entries, SL hits, stale-thesis pings) and cannot receive `/confirm`, `/close`, `/panic`. If an entry fires between now and fix, user gets nothing.

bot.err: all `MallocStackLogging: can't turn off...` — harmless macOS debug noise from Python subprocesses, not actionable.

## Gate behavior
`gate_weekly_trend`: N=1, FNR=1.0 (blocked 1 trade that would have hit TP1). Below `MIN_SAMPLE_SIZE_FOR_TUNING=20` — no action warranted, flag for tracking.

`gate_no_entry_zone`: N=73, FNR=0.0 — well-calibrated.

## Recommended actions
1. **Investigate DNS / network** — `nslookup api.telegram.org` from the host. If DNS is down, check system resolver or restart networking. Telegram silence = zero operational visibility until fixed.
2. **Verify heartbeat resumes** — once network is confirmed up, check that the main loop processes the 08:00–09:00 morning-prep window and writes a fresh `bot.heartbeat_ts`. If heartbeat stays stale after XETRA open, the loop is stuck.
3. **Track weekly_trend FNR** — at N=1 it's anecdotal. When N reaches 5+, revisit `gate_false_negative_rates()` output before any tuning.