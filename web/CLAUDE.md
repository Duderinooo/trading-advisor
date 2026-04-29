@AGENTS.md

# web/ — Trading Advisor Dashboard

Local-only, read-only dashboard for the Python bot in the parent dir. Single user, runs on `localhost:3000` via `npm run dev`. No deploy target, no prod build pipeline (yet).

> Read `docs/ARCHITECTURE.md` for system overview and `docs/CONVENTIONS.md` for React/Next.js patterns used in this codebase **before writing or editing components**. Both files are short and load-bearing.

## Stack pinned in [package.json](package.json)

- Next.js **16.2** (App Router, Turbopack)
- React **19.2** (with React Server Components + `use` hook)
- Tailwind **v4** (CSS-first config, no `tailwind.config.js`)
- TypeScript **5** (strict)
- Recharts **3** (charts)
- Node **20+**

This is a recent Next.js — APIs differ from training data. `AGENTS.md` requires reading `node_modules/next/dist/docs/01-app/` before non-trivial changes.

## Critical invariants

1. **Read-only.** No write paths (no POST/PUT/DELETE handlers, no Server Actions that mutate). Bot owns [../portfolio.json](../portfolio.json) under `core.portfolio.portfolio_lock`. Web racing the lock = corruption. If write features arrive, route through Telegram (see [../CLAUDE.md](../CLAUDE.md) "telegram_listener"), never direct fs.
2. **Server reads fs, client never does.** [lib/portfolio.ts](lib/portfolio.ts) uses `node:fs` and is import-only from server components / route handlers. [lib/compute.ts](lib/compute.ts) is pure and isomorphic — safe in `"use client"`.
3. **Path security on log/file routes.** Any new route that reads files MUST use a `Set<string>` allowlist of basenames (see [app/api/logs/route.ts](app/api/logs/route.ts)). Never accept `path` from query string.
4. **Hydration-safe.** Don't initialize `useState` with `new Date()`, `Math.random()`, `Date.now()`, or `localStorage`. Set such values in `useEffect`. Wrap unavoidable time strings in `<span suppressHydrationWarning>`.
5. **Cache-stable SSR.** Pages that read `portfolio.json` declare `export const dynamic = "force-dynamic"` so Next.js doesn't statically prerender stale data.
6. **Tailwind v4 syntax.** Theme tokens via `@theme` in [app/globals.css](app/globals.css), no JS config file. Don't add `tailwind.config.js` — won't be read.

## Data flow (read-only loop)

```
portfolio.json (Python writes under lock)
        │
        ▼
lib/portfolio.ts  (server, fs.readFile)
        │
        ├── app/page.tsx   (initial SSR render)
        │       │
        │       ▼
        │  components/Dashboard.tsx ("use client")
        │       │   useState(initial)  →  setInterval(refresh, 30s)
        │       ▼
        └── app/api/portfolio/route.ts  ←  client fetch
                │
                ▼
            JSON  →  lib/compute.ts (pure: equity, stats, exposure)
                │
                ▼
            Recharts / tables (client components)
```

The same compute functions run server-side for first paint and client-side after every poll. They're pure, no fs, safe for both. If you need new derived data, add it to [lib/compute.ts](lib/compute.ts), not into the page.

## Adding features — rules

- **New chart / widget** → client component under [components/](components/). Receive raw data via props, derive locally with `useMemo`. Don't fetch inside the widget; lift fetching to [Dashboard.tsx](components/Dashboard.tsx).
- **New API route** → `app/api/<name>/route.ts` with `export const dynamic = "force-dynamic"`. Read via [lib/portfolio.ts](lib/portfolio.ts), never reimplement fs access.
- **New page** → `app/<name>/page.tsx`. If it shows portfolio data, follow the `page.tsx → Dashboard.tsx` server/client split pattern. If it's static (about, settings), keep it as a server component.
- **New derived metric** → add to [lib/compute.ts](lib/compute.ts) so server SSR and client polling produce identical output. Mirror the Python source of truth in [../core/portfolio.py](../core/portfolio.py) — don't invent new metrics here without a Python counterpart.
- **No new dependencies without justification.** Recharts + date-fns + Next/React/Tailwind is the budget. Anything else needs a one-line `# Why:` in the PR.

## Don't

- Don't add Vercel-isms (`@vercel/analytics`, edge runtime, ISR-based caching) — local-only.
- Don't import from `node:fs`, `node:path`, `node:child_process` in any file under [components/](components/) or any file with `"use client"`.
- Don't pass non-serializable props (Date, Map, functions) from server components to client components — Next.js will warn but it gets messy. Stringify on server, parse on client if needed.
- Don't use `next/image` for charts or icons — overkill. Inline SVG or Recharts only.
- Don't write tests yet. There are none. If you need verification, type-check (`npx tsc --noEmit`) and curl the routes (see "Verify" below).
- Don't add a `web/.env*` for secrets — there are none. Telegram tokens live in the parent project.

## Verify

No test suite. Verification recipe:

```bash
npx tsc --noEmit                                           # type check
npm run dev > /tmp/web.log 2>&1 &
curl -s -o /dev/null -w "%{http_code}\n" localhost:3000    # 200
curl -s -o /dev/null -w "%{http_code}\n" localhost:3000/logs
curl -s localhost:3000/api/portfolio | head -c 200
pkill -f "next dev"
```

Hydration mismatches won't show up in curl — open the browser console once after edits to client components.

## When in doubt

- Architecture / data flow → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- React/Next 16 patterns specific to this codebase → [docs/CONVENTIONS.md](docs/CONVENTIONS.md)
- Next.js 16 official guide → [node_modules/next/dist/docs/01-app/](node_modules/next/dist/docs/01-app/) (read it; APIs may differ from your training data)
- Bot logic, invariants, lock semantics → [../CLAUDE.md](../CLAUDE.md)
