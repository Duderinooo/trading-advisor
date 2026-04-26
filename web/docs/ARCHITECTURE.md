# Architecture — web/

Read-only Next.js 16 dashboard sitting next to the Python trading bot. Reads [../portfolio.json](../portfolio.json) directly via `node:fs`. No backend service, no DB, no auth.

## System context

```
┌──────────────────────────────────────────────────────────────────┐
│  trading-advisor/  (monorepo-ish, two siblings)                  │
│                                                                  │
│   ┌─────────────────────────┐         ┌──────────────────────┐   │
│   │  Python bot             │  writes │  portfolio.json      │   │
│   │  main.py                │ ───────►│  (under RLock)       │   │
│   │  telegram_listener.py   │         └──────────┬───────────┘   │
│   │  core/                  │                    │ reads         │
│   └────────────┬────────────┘                    │               │
│                │ writes                          │               │
│                ▼                                 ▼               │
│         bot.log / bot.err            ┌──────────────────────┐    │
│                ▲                     │  web/  (Next.js)     │    │
│                └─────── reads ───────│  localhost:3000      │    │
│                                      └──────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

The Python side is the source of truth. Web reads its files. Web never writes. If web needs to *cause* a change, it would do so by sending a Telegram message that the bot's `telegram_listener` thread picks up and processes under `core.portfolio.portfolio_lock` — but that path is **not implemented yet**.

## Directory layout

```
web/
├── app/                        Next.js App Router
│   ├── layout.tsx              root layout, fonts, body wrapper
│   ├── page.tsx                / — dashboard server entry, SSRs initial portfolio
│   ├── globals.css             Tailwind v4 import + theme tokens
│   ├── logs/page.tsx           /logs — bot.log / bot.err tailer
│   └── api/
│       ├── portfolio/route.ts  GET → full Portfolio JSON
│       ├── equity/route.ts     GET → EquityPoint[]
│       ├── stats/route.ts      GET → HitStats | null
│       └── logs/route.ts       GET ?file=&lines= → tail (allowlisted)
│
├── components/                 (React)
│   ├── Dashboard.tsx           "use client" — top-level orchestration, polling
│   ├── EquityChart.tsx         "use client" — Recharts + timeframe filter
│   ├── LogsViewer.tsx          "use client" — log tail UI
│   ├── OpenTrades.tsx          server-renderable table
│   ├── ClosedTrades.tsx        server-renderable table
│   ├── WatchLevels.tsx         server-renderable list
│   ├── Stats.tsx               server-renderable hit-stats panel
│   └── Card.tsx                Card + Kpi primitives
│
├── lib/
│   ├── types.ts                shared TS types (Portfolio, ClosedTrade, …)
│   ├── portfolio.ts            SERVER ONLY — fs.readFile + re-export of compute
│   └── compute.ts              ISOMORPHIC — pure: equity, stats, exposure, filter
│
├── docs/
│   ├── ARCHITECTURE.md         this file
│   └── CONVENTIONS.md          React/Next/Tailwind best practices
│
├── CLAUDE.md                   project rules for AI agents (imports AGENTS.md)
├── AGENTS.md                   "this Next.js != your training data" reminder
└── package.json
```

## Boundary: server vs client

| Module | Runs on | May import |
|---|---|---|
| `app/page.tsx`, `app/logs/page.tsx` | Server (RSC) | anything |
| `app/api/**/route.ts` | Server (Node runtime) | anything |
| `lib/portfolio.ts` | Server only | `node:fs`, `node:path`, `lib/compute.ts` |
| `lib/compute.ts` | Both | only pure TS, no fs/no DOM |
| `lib/types.ts` | Both | nothing (types only) |
| `components/Dashboard.tsx`, `EquityChart.tsx`, `LogsViewer.tsx` | Client (after hydration) | `lib/compute.ts`, `lib/types.ts`, React, Recharts |
| `components/OpenTrades.tsx` etc | Either (no `"use client"`) | `lib/types.ts`, React |

The split exists because [lib/portfolio.ts](../lib/portfolio.ts) imports `node:fs` — Next.js will throw at build time if a client component transitively imports it. Compute logic is duplicated nowhere; it lives once in [lib/compute.ts](../lib/compute.ts) and runs both at SSR time and during client polling.

## Data flow (one full refresh cycle)

```
                 t=0  GET /
┌──────────────────────────────────────────────────────────────┐
│  Server                                                      │
│   app/page.tsx (async server component)                      │
│      └─ readPortfolio()  → fs.readFile portfolio.json        │
│         renders <Dashboard initial={portfolio} />            │
│         streams HTML to browser                              │
└──────────────────────────────────────────────────────────────┘
                       │
                       ▼  HTML + serialized props
┌──────────────────────────────────────────────────────────────┐
│  Browser                                                     │
│   React hydrates Dashboard with `initial`                    │
│   useEffect: setInterval(refresh, 30_000)                    │
│   useMemo: equity / curve / stats derived from `portfolio`   │
└──────────────────────────────────────────────────────────────┘
                       │
                       ▼  every 30 s
┌──────────────────────────────────────────────────────────────┐
│  GET /api/portfolio (cache: "no-store", dynamic)             │
│   Server route handler:                                      │
│      readPortfolio() → JSON                                  │
│   Browser: setPortfolio(data) → re-render derived values     │
└──────────────────────────────────────────────────────────────┘
```

`/api/equity` and `/api/stats` exist for external consumers (curl, future tools) but the dashboard itself does not call them — the client computes from the full portfolio because we already have it. They use the same lib functions; output is identical.

## Compute model

All derived values live in [lib/compute.ts](../lib/compute.ts):

- `computeEquityCurve(portfolio)` — walks `closed_trades` sorted by `exit_date`, accumulates `pnl_eur` onto `total_capital_eur`. Mirror of `core/portfolio.py::_equity_curve`.
- `computeHitStats(closed)` — win rate, R-multiple, conviction breakdown, Brier calibration, mistake-class distribution. Mirror of `core/portfolio.py::compute_hit_stats`.
- `currentEquity`, `openExposure` — KPI helpers.
- `filterEquityByTimeframe(curve, "1D"|"1W"|"1M"|"1Y"|"ALL")` — anchored slicing so a partial window doesn't visually start at zero.

Mirror the Python definitions when the bot's logic changes; if numbers diverge between Telegram messages and the dashboard, that's a porting bug. Keep these functions pure and dependency-free so they can run in both runtimes.

## Why no Python bridge / FastAPI

Considered, rejected for now:

- The bot already serializes everything we need to disk under a lock. Reading the JSON is atomic enough for read-only display (worst case: dashboard shows portfolio between two writes, refreshes within 30 s).
- Adding FastAPI or a websocket means a second process to run, deploy, and supervise — for a single-user local dashboard.
- A Python bridge would be required for *write* paths (confirm/close trades). When that arrives, the right design is: web → Telegram Bot API (sends user-typed `/confirm`) → existing `telegram_listener` → existing lock-protected writers. Web stays read-only.

## Failure modes

| Symptom | Likely cause |
|---|---|
| Hydration mismatch warning | Client component initialized state with `new Date()`, `Math.random()`, locale-formatted string, or `localStorage`. Move to `useEffect`. |
| `Module not found: node:fs` in client bundle | A client component imported [lib/portfolio.ts](../lib/portfolio.ts) instead of [lib/compute.ts](../lib/compute.ts). |
| Stale data after refresh | Route handler missing `export const dynamic = "force-dynamic"`. |
| `EACCES` reading portfolio.json | Run dev server as same user that owns the file. Don't `sudo npm run dev`. |
| `/api/logs` 400 | File not in `ALLOWED` set. Don't extend the set casually — log files can leak secrets (Telegram tokens land in `bot.err`). |
