# Health Report 2026-05-31T20:21 CET

## Overall: 🟢 healthy
Transient DNS outage at ~19:04 (Telegram + ls-tc.de) self-resolved; heartbeat fresh at 41s lag; all DBs clean; no open positions.

## Process hygiene
Clean hierarchy: `run.sh` (1881) → `caffeinate -i` (1901) → `main.py` (1915) → `caffeinate -is -w 1915` (1916). No orphans, no duplicate instances.

## Pipeline freshness
Heartbeat age 41s at report time. Market hours = false (Saturday evening). `api_calls_today=1` — correct for weekend (morning run only, no market activity).

## LLM channel
Last agent run: haiku/event id=220 at ~19:49, exit_code=0. Error rate: 0/20 recent runs. Model mix nominal (haiku for event, sonnet for skills).

## Data quality
DNS failures at 19:04:
- `telegram.error.NetworkError: httpx.ConnectError: [Errno 8]` — Telegram polling lost DNS
- `LS-TC quote fetch failed` IE00BMTM6B32 + DE000CBK1001 — same DNS outage

Both recovered before next poll cycle (heartbeat fresh, last event run at 19:49 succeeded). No data corruption. Outage window ~1 cycle.

## State integrity
`bot.db`: ok. `agent_runs.db`: ok. All 4 brain traces present (morning, opening-XETRA, opening-US, event), zero warnings each.

## Gate behavior
`gate_weekly_trend`: FNR=1.0 — 1 block, 1 would-have-won. N=1, below MIN_SAMPLE_SIZE_FOR_TUNING=20. Not actionable yet; watch for accumulation.

`gate_no_entry_zone`: FNR=0.0, N=73. Performing correctly.

## Recommended actions
1. **Monitor `gate_weekly_trend` FNR** — currently N=1/FNR=1.0. No tuning yet (N<20), but log this as the first data point. When N≥20 revisit against `gate_false_negative_rates()`.
2. **Investigate DNS outage root cause** — two separate hosts (api.telegram.org, www.ls-tc.de) failed simultaneously at 19:04. Likely transient ISP/macOS DNS hiccup. If recurs: check `/etc/resolv.conf`, consider adding DNS fallback (`8.8.8.8`) to network config.
3. **`bot.err` MallocStackLogging noise** — cosmetic macOS Python subprocess artifact. Suppress with `MALLOC_STACK_LOGGING=0` in `run.sh` env if log noise becomes signal-obscuring.