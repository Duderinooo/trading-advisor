# Conventions — React 19 + Next.js 16 patterns used in this codebase

Short, opinionated, project-specific. Not a general Next.js tutorial. Read [ARCHITECTURE.md](ARCHITECTURE.md) first.

## Server Components vs Client Components

**Default: Server Component.** No `"use client"` directive. Runs once on the server per request, sends HTML, doesn't ship JS for itself.

Add `"use client"` only when you need:
- React hooks (`useState`, `useEffect`, `useMemo`, `useRef`, `use`)
- Event handlers (`onClick`, `onChange`)
- Browser APIs (`window`, `document`, `localStorage`)
- Third-party libs that depend on the above (Recharts → client)

**Push the boundary down.** Make the *leaf* a client component, keep parents server. Examples in this repo:

- [app/page.tsx](../app/page.tsx) — server component, does fs read, passes data to client.
- [components/Dashboard.tsx](../components/Dashboard.tsx) — `"use client"` because it polls and holds state.
- [components/OpenTrades.tsx](../components/OpenTrades.tsx) — no directive, renders pure markup; works in both contexts. Imported by a client parent (Dashboard) so it ends up in the client bundle, but it stays trivially serializable and would also work in a server-only page.

Never put `"use client"` on a file that imports from [lib/portfolio.ts](../lib/portfolio.ts) — `node:fs` will break the client bundle. Use [lib/compute.ts](../lib/compute.ts) instead.

## Data fetching

In server components: just `await` it.

```tsx
// app/page.tsx
export const dynamic = "force-dynamic";

export default async function Home() {
  const initial = await readPortfolio();      // fs read
  return <Dashboard initial={initial} />;
}
```

In client components: `fetch` inside `useEffect` or in event handlers. Don't fetch during render.

```tsx
// components/Dashboard.tsx
useEffect(() => {
  if (!autoRefresh) return;
  const id = setInterval(refresh, 30_000);
  return () => clearInterval(id);
}, [autoRefresh, refresh]);
```

Don't introduce SWR, React Query, or tRPC for a single endpoint polled every 30 seconds. Plain `fetch` + `useState` is fine. Re-evaluate if we hit 5+ endpoints.

## `dynamic = "force-dynamic"` is mandatory on data routes

Every route handler and every page that reads `portfolio.json` exports:

```ts
export const dynamic = "force-dynamic";
```

Otherwise Next.js may statically prerender, freezing the dashboard at build time. The trade-off (no caching) is fine because we're local-only with one user.

## Hydration safety

React 19 SSR + hydration is strict. The server-rendered HTML must equal the first client render. Things that break it:

| Bad | Fix |
|---|---|
| `useState(new Date())` | `useState<Date \| null>(null)` + `useEffect(() => setX(new Date()), [])` |
| `useState(Math.random())` | same pattern |
| `useState(() => localStorage.getItem("x"))` | same pattern |
| `<span>{date.toLocaleTimeString()}</span>` (locale differs) | wrap with `<span suppressHydrationWarning>` once value is client-only |
| Conditional `if (typeof window !== "undefined")` in render | move to `useEffect` |

Pattern in [components/Dashboard.tsx](../components/Dashboard.tsx):

```tsx
const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
useEffect(() => { setRefreshedAt(new Date()); }, []);
// later:
<span suppressHydrationWarning>
  {refreshedAt ? refreshedAt.toLocaleTimeString() : "—"}
</span>
```

## Route handlers

```ts
// app/api/<name>/route.ts
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  // optional: read query
  // const url = new URL(req.url); const x = url.searchParams.get("x");
  return Response.json(payload);
}
```

- One file = one route. Methods are exported by HTTP verb (`GET`, `POST`, …).
- For typed dynamic params, use Next 16's `RouteContext<'/users/[id]'>` global helper (see `node_modules/next/dist/docs/01-app/01-getting-started/15-route-handlers.md`).
- Validate query input. For file paths, use a `Set<string>` allowlist (see [app/api/logs/route.ts](../app/api/logs/route.ts)).

## Tailwind v4

CSS-first config in [app/globals.css](../app/globals.css):

```css
@import "tailwindcss";

@theme inline {
  --color-background: var(--background);
  --font-sans: var(--font-geist-sans);
}
```

- No `tailwind.config.js`. Adding one will not be read.
- Use Tailwind utility classes inline. Don't extract into CSS modules unless a class chain repeats 4+ times.
- Color tokens used here: `zinc-*` for surfaces, `emerald-*` for positive PnL, `rose-*` for negative, `amber-*` for warnings. Stay in this palette for visual coherence.

## Component patterns from this repo

### KPI / Card primitives — [components/Card.tsx](../components/Card.tsx)

`<Card title="...">` for sectioning, `<Kpi label value sub tone />` for metric tiles. Reuse before inventing new wrappers.

### Tone prop pattern

Status colors via a small union, not boolean flags:

```tsx
tone?: "neutral" | "good" | "bad" | "warn"
```

Cleaner than `isPositive={true}` once a third state exists (warn/neutral). Used in `Card.tsx` and `Stats.tsx`.

### Derived values via `useMemo`

Anything computed from `portfolio` in a client component is wrapped:

```tsx
const curve = useMemo(() => computeEquityCurve(portfolio), [portfolio]);
```

Polling causes a state update every 30 seconds. Without `useMemo`, every dependent render redoes O(n) work over closed trades. Keep this discipline as the trade history grows.

### Filter UI inside the chart, not above

[EquityChart.tsx](../components/EquityChart.tsx) owns its own timeframe state. The parent passes the *full* curve; the chart slices it. This keeps Dashboard simple and lets the chart be reused elsewhere with no parent coordination.

## TypeScript

- `strict: true` in [tsconfig.json](../tsconfig.json). Don't disable.
- Prefer type aliases (`type Portfolio = …`) over interfaces. Consistency only — no semantic difference here.
- Don't `as` cast unless narrowing from `unknown`. If you need `as`, explain why with a one-line comment.
- Don't introduce `any`. If a Recharts callback type is annoying (e.g. `Formatter<ValueType, NameType>`), let the params be inferred and `String(...)` / `Number(...)` inside the body — see the tooltip formatter in [EquityChart.tsx](../components/EquityChart.tsx).

## Don't

- **Don't `next/image` for icons.** Inline SVG. `next/image` is for content imagery and requires server config.
- **Don't `next/font`** beyond the existing `Geist` setup unless you have a reason.
- **Don't add Server Actions** until we actually mutate something. They're for `<form action={…}>`, not for data fetching.
- **Don't introduce middleware** for "auth" — there's no auth, it's localhost.
- **Don't add a state library** (Zustand, Redux, Jotai). One client component holds the portfolio in `useState`. If state needs to span pages, lift to a Context provider in `layout.tsx`.
- **Don't add ESLint configs** unless you also wire it into the dev workflow. Ornamental config is worse than none.

## Verifying changes

```bash
npx tsc --noEmit                                            # types
npm run dev > /tmp/web.log 2>&1 &
sleep 4
curl -sI http://localhost:3000/ | head -1                   # 200
curl -s http://localhost:3000/api/portfolio | head -c 200   # JSON
# Open http://localhost:3000 in a browser, check console for hydration warnings
pkill -f "next dev"
```

Hydration warnings only show in the browser, not in `curl`. Always actually load the page after editing client components.
