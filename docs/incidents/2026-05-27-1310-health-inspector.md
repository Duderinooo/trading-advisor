# Health Report 2026-05-27T13:09:22+02:00

## Overall: 🔴 critical
JSON schema strictTypes error on `new_take_profit` field blocks all opening + event LLM calls since 09:10; only 3 API calls succeeded today during full market hours.

---

## LLM channel

**Schema error — recurring, total blocker:**
```
AgentRunError: claude CLI exit=1: strict mode: use allowUnionTypes to allow union type keyword
at "#/properties/actions/items/oneOf/2/properties/input/properties/new_take_profit" (strictTypes)
```
- First hit: ~09:10 (opening mode, XETRA)
- Second hit: 09:25:36 (event mode, 3OIL.MI big-mover -9.6%)
- `api_calls_today=3` at 13:09 CET = ~4h into market hours. With 15-min polling cadence, should be 15+ calls. Delta = blocked.
- `new_take_profit` in the tool schema at `core/llm/prompt/prompts/tools.py` has a union type (`Optional[float]` or similar) that Claude CLI's strict JSON Schema validator rejects. Every call using that tool schema fails.
- Consequence: 3OIL.MI -9.6% big-mover went unanalyzed. All event-triggered analysis since 09:25 silently fails.

---

## Pipeline freshness

Heartbeat age=21s — loop alive. But cadence is hollow: loop ticks, Claude calls fail, no entries possible.

---

## Process hygiene

PID 98366 `caffeinate -i` PPID=1, PGID=98364, elapsed 1d 13h 56m — orphan from previous session, not in bot's process group (57574). Not harmful but should be cleaned.

---

## Open-position aging

MBG.DE entered 2026-05-21 14:32 = 6 days held. TP1 hit logged at 09:10:25 today — BE-shift + trail should be active. Position still open, trail running. Not stale per se (TP1 hit = live runner), but worth confirming trail level is sensible given schema errors may have prevented any update recommendation reaching Claude.

---

## Gate behavior

`gate_weekly_trend`: FNR=1.0 (1/1 blocked trade would have won). N=1, below tuning threshold — log for future review. Not actionable yet.

---

## Recommended actions

1. **Fix schema immediately** — `new_take_profit` in `core/llm/prompt/prompts/tools.py` (tool `oneOf[2]`, the update action) has a union type. Replace `anyOf`/`oneOf` with explicit nullable: `{"type": ["number", "null"]}` or use `allOf` pattern, or add `"allowUnionTypes": true` if the CLI supports it. Every event+opening call fails until fixed.

2. **Kill orphan** — `kill 98366` (just a caffeinate, safe to terminate).

3. **After schema fix** — trigger manual `/morning` or event re-run to process any missed signals from the 09:10–now window. 3OIL.MI geo event at -9.6% was never analyzed.

4. **Log `gate_weekly_trend` N=1 FNR=1.0** for future tracking — revisit when N≥20.