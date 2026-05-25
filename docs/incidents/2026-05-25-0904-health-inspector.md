# Health Report 2026-05-25T09:03:11+02:00

## Overall: 🟡 degraded
Bot just restarted (3rd time today, latest at 09:03:11); heartbeat ts=null; morning prep coverage uncertain due to Telegram-conflict period + rapid restart cycle.

---

## Process hygiene
Process tree clean: run.sh (PID 36837) → caffeinate -i (36839) → main.py (36841) → caffeinate -is -w (36842). Proper double-caffeinate pattern, no orphans.

**Rapid-restart flag**: 3 distinct starts today in 20 minutes:
- 08:44:46 (after Telegram conflict period)
- 09:00:43 (2.5 min later)
- 09:03:11 (current, 2.5 min later again)

Cause of 09:00 and 09:03 restarts is **not logged** — no error or shutdown reason visible beyond `👋 Shutting down`. Manual kills or silent crash before log flush.

---

## Pipeline freshness
`heartbeat.ts = null` — key exists in kv_state but timestamp never written. Bot has been alive ~2 min so first tick may not have fired yet; however all 3 starts today would have had the same null if the write is broken. Unresolvable from this snapshot alone — monitor next run.

Morning prep at 08:00 likely missed by current instance (started 09:03). Prior instance (started 08:44) *could* have run it if `last_morning_prep_date` wasn't already set from the conflict-era instance. Log tail doesn't show any morning prep completion line from any of today's starts. XETRA opening check (09:05) is 2 min out from snapshot time — will fire in current instance.

---

## LLM channel
agent_runs IDs 60–69 are all zero-duration test fixtures (`mode="test"`, `"t"`, instant exits). Only real production run in recent 50 is **id=70** (`skill:bug-watcher`, haiku, 21.6s, exit_code=0). No morning/event/opening Claude calls visible in agent_runs since restarts began. Consistent with a bot that hasn't completed a full tick cycle yet today.

---

## State integrity
Both `bot.db` and `agent_runs.db` pass `PRAGMA integrity_check`. Brain traces (morning + event) present with zero warnings — but no timestamps in trace payload, so staleness unverifiable.

---

## Open-position aging
2 positions: BAYN.DE, MBG.DE — oldest entry 2026-05-21 13:35 = **4 calendar days**. Sunday 25.05 is a holiday (Pfingstmontag / Whit Monday in Germany — XETRA closed). Effective trading days since entry: ~3. Stale-thesis alert threshold depends on per-rec `hold_days_max`; worth checking if either rec has a short hold window.

---

## Anomalies
**Telegram Conflict storm (08:12–08:13)**: `telegram.error.Conflict: terminated by other getUpdates request` — two bot instances were polling simultaneously. The pre-08:44 instance was not cleanly terminated before the 08:44 start. This is the root of today's instability.

**Restart loop without logged cause**: 09:00:43 and 09:03:11 starts each preceded by `👋 Shutting down` with no error trace. Either manual `/restart`-style kills or a crash before the logger flushed.

**Gate-FNR empty `{}`**: Could be normal (no blocked entries yet recorded) or could indicate `record_blocked_entry` telemetry not firing. No way to distinguish from this snapshot.

---

## Recommended actions

1. **Verify morning prep ran today**: Check `portfolio.json → last_morning_prep_date`. If not `2026-05-25`, send `/morning` via Telegram to force it (uses `bypass_cooldown=True` path). Critical — opening check at 09:05 is imminent and depends on morning context.

2. **Investigate restart causes**: `grep "Shutting down\|Traceback\|ERROR" bot.log` around 09:00 and 09:03 to find what triggered both short-lived instances. Two 2.5-min runs in a row suggests same crash reproduced.

3. **Watch heartbeat**: After next full 15-min tick (≈09:18), re-check `bot.heartbeat_ts`. If still null, heartbeat write in the main loop is silently failing — find and fix the write path.

4. **Confirm no zombie Telegram instance**: `ps aux | grep python` — verify only PID 36841 is running. The Conflict error self-resolved (new instance won the poll slot), but confirm no stale process survived.

5. **BAYN.DE + MBG.DE**: Check `hold_days_max` on both recs. If either is ≤4, stale-thesis alert should already have fired (once-per-day logic). If not seen on Telegram, stale-thesis check may have been skipped in today's chaotic restart cycle.