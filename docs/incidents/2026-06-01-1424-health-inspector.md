# Health Report 2026-06-01T14:23:37+02:00

## Overall: 🟡 degraded
Morning brain skipped `set_watch_levels` despite explicit stale-ticker finding; Haiku output tokens running 3–4× budget across event/opening calls.

## Pipeline freshness
Heartbeat age 42s at report time. 5 API calls today. All 4 trace slots populated (morning, opening×2, event). Pipeline alive.

## LLM channel
Three Haiku overruns this session — all non-truncated but well above 900-tok budget:
- `09:45` event: `out_tok=4025/900` (+347%)
- `09:10` opening: `out_tok=2876/900` (+219%)
- `08:09` event: `out_tok=1258/900` (+40%)

Morning Sonnet also over: `4416/3500` (+26%).

All exit_code=0 — no failures, but consistent overrun suggests context size or Haiku verbosity is unchecked.

## State integrity
DBs pass integrity checks. One note:

Morning trace (08:02:21) logged:
```
[ERROR] TRACE [morning]: set_watch_levels NOT called
```
Text preview: `"PASS. No A+ setups. CBK stale — price €37.10 above old zone, quality D, drop watch."` — Sonnet identified CBK.DE as drop-worthy but emitted no tool call. CBK.DE remains in watch_levels with a stale thesis.

## Gate behavior
`gate_weekly_trend`: FNR=1.0 (1/1 block would have won). N=1, below `MIN_SAMPLE_SIZE_FOR_TUNING=20` — no action, but flag for accumulation. `gate_no_entry_zone` clean (73 blocks, 0 wins). `gate_red_team` 1 ambiguous, 0 FN.

## Anomalies
- `www.ls-tc.de` DNS resolution failure (2026-05-31 19:04) — isolated, not repeated today. Transient network issue or upstream outage.
- `bot.err` is 100% `MallocStackLogging: can't turn off` noise from macOS — harmless system artifact from Python subprocesses.
- `api_calls_today=5` at 14:23 during market hours — low relative to event volume (3OIL.MI big mover, SAP +7.5%, opening check). Consistent with Haiku handling all non-morning calls.

## Recommended actions
1. **Investigate set_watch_levels skip** — morning Sonnet said "drop CBK" but called no tool. Check if `tool_called=False` + `stop=end_turn` path in `agents/_lib/runner.py` or prompt suppresses the call when model emits PASS. CBK.DE is currently a stale watch entry blocking a slot. Manual `/morning` or check `core/llm/prompt/prompts/tools.py` whether set_watch_levels is callable in PASS-path.
2. **Haiku token budget** — `out_tok=4025` on a 900-budget event call is 4.4×. Either the system prompt grew or Haiku ignores the budget hint. Run `grep -n "max_tokens\|out_tok" agents/_lib/runner.py` and compare event/opening max_tokens config vs morning. Trim if context bloat is the cause.
3. **Monitor gate_weekly_trend FNR** — currently 1.0 at N=1. Add a mental note; at N=20 run `gate_false_negative_rates()` again. If still ≥0.5, write a research note before tuning.