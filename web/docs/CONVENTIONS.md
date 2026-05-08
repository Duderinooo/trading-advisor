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

The full smoke recipe is captured in the [verify-web skill](../.claude/skills/verify-web/SKILL.md) — invoke it after non-trivial changes.

---

# Best Practices

This section is the working memory of "stuff we'd otherwise re-derive every time we add a feature." It complements the conventions above with concrete templates, framework gotchas, and pitfalls. Skim it before starting non-trivial work.

## React 19 specifics

### `import type` discipline

Type-only imports are stripped at compile time and don't ship to the client bundle:

```ts
// ✅ Type lives only in the IDE
import type { Portfolio, ClosedTrade } from "@/lib/types";

// ❌ Drags the whole `types.ts` module into the client bundle
import { Portfolio } from "@/lib/types";
```

`types.ts` is small and treeshakeable, so this is a discipline not a hard rule — but follow it for consistency.

### `use()` hook (React 19)

Unwraps a promise at render time. Useful when:
- Server passes a promise via prop, client unwraps lazily
- Conditional fetch in a client component (don't pre-await in parent)

We don't use it yet — initial portfolio is awaited in `app/page.tsx` and passed as fully-resolved JSON. If a tab grows expensive enough to defer until it's clicked, `use(somePromise)` is the right tool. Don't introduce SWR for this.

### `useTransition` / `useDeferredValue`

The dashboard polls every 30s and recomputes equity curve / hit stats / setup stats. With ~3 closed trades this is instant; with 200+ it can stutter. Pre-empt by wrapping the recomputation:

```tsx
const [isPending, startTransition] = useTransition();
const refresh = useCallback(async () => {
  const res = await fetch("/api/portfolio");
  const next = await res.json();
  startTransition(() => setPortfolio(next));   // non-blocking
}, []);
```

Or `useDeferredValue` if a single derived value is expensive. **Don't add prematurely** — measure first. The repo has 3 closed trades; this is a "when N>50 closed_trades" concern.

### `React.memo` vs `useMemo`

| Need | Tool |
|---|---|
| Cache a derived value (object, array, computed number) | `useMemo` |
| Skip re-rendering a child when its props haven't changed | `React.memo(Child)` |

The codebase uses `useMemo` heavily. `React.memo` is currently unused — Recharts components are big but already optimize internally; wrapping our table components is wasteful unless props change every poll (they don't, since `useMemo` keeps refs stable).

### `Suspense` boundaries

We don't stream yet — `app/page.tsx` resolves `readPortfolio()` before rendering `Dashboard`. If a future page reads multiple files (e.g. `/training` reads paper portfolio + closed_trades), wrap each section in `<Suspense fallback={<Skeleton />}>` and let Next stream them independently. Co-locate `loading.tsx` next to the `page.tsx` for the route-level fallback.

## Next.js 16 specifics

### Streaming + `loading.tsx`

Next 16 streams Server Components by default when there's a `loading.tsx`. Pattern for a new route:

```
app/
  brain/
    page.tsx          ← async server component, awaits data
    loading.tsx       ← shows while page.tsx is awaiting (skeleton/spinner)
    error.tsx         ← shows if page.tsx throws (currently missing repo-wide)
```

We currently have `app/page.tsx` with no `loading.tsx` because the fs read is fast. Add for any new page that does a slower read or external HTTP.

### `error.tsx` boundary (recommended addition)

Currently any throw in a client component crashes the whole page. Add `app/error.tsx` to catch + show a fallback:

```tsx
// app/error.tsx
"use client";
export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="p-6">
      <h2 className="text-rose-400">Etwas ist kaputt</h2>
      <pre className="text-xs text-zinc-500 mt-2">{error.message}</pre>
      <button onClick={reset} className="mt-3 px-3 py-1 rounded bg-zinc-800">Reload</button>
    </div>
  );
}
```

### `not-found.tsx`

None currently. Add when first 404 path arises (e.g. `/trade/[id]` with stale ID).

### Server Actions — when

Don't add until we mutate from the browser. The architecture routes mutations through Telegram (Python owns the lock). If we ever add a "kill switch" button on the dashboard, it should call an HTTP route that posts to the bot's Telegram-thread, not write `portfolio.json` from the web side.

### Route handlers — typed dynamic params

```ts
// app/api/trades/[id]/route.ts
export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/trades/[id]">
) {
  const { id } = await ctx.params;   // params is async in Next 16
  // ...
}
```

`RouteContext` is a global helper in Next 16. Don't import it.

### `<Link>`

Multi-page nav doesn't exist yet (`/`, `/brain`, `/logs`). When it does:

```tsx
import Link from "next/link";
<Link href="/brain" prefetch={false}>Brain</Link>
```

`prefetch={false}` for tabs that only some users open (saves bandwidth). Default `prefetch` is fine for primary nav.

### `next.config.ts`

Currently empty by design — no env vars, no rewrites, no custom webpack. Add only when there's a concrete need (e.g. proxying to a different bot port). Don't add ornamentally.

## Performance

### Recharts re-paint loop

Recharts re-paints when its data prop has a new reference, even if values are identical. To prevent every poll triggering a chart redraw:

```tsx
// Round live equity to whole € so the curve key only changes when €1+ moves
const liveEquityWhole = useMemo(() => liveEquityRounded(portfolio), [portfolio]);
// Pass curveKey to Recharts; only triggers re-mount when it actually changes
<EquityChart curve={curve} liveKey={liveEquityWhole} />
```

The helper `liveEquityRounded(p)` is in [lib/compute.ts](../lib/compute.ts). Use the same pattern for any other live-data Recharts mount.

### `useMemo` is the canonical pattern

Anything derived from `portfolio` in a client component must be wrapped:

```tsx
const stats = useMemo(
  () => computeHitStats(portfolio.closed_trades, portfolio.cash_movements ?? []),
  [portfolio.closed_trades, portfolio.cash_movements],
);
```

Polling triggers a state update every 30s. Without `useMemo`, every dependent render re-derives.

### `Date.now()` / `new Date()` in render

Both create a new value every render → defeats memoization, breaks hydration. Always:

```tsx
// ❌ Render-time clock
<span>{new Date().toLocaleTimeString()}</span>

// ✅ State-bound clock, set in effect
const [now, setNow] = useState<Date | null>(null);
useEffect(() => {
  setNow(new Date());
  const id = setInterval(() => setNow(new Date()), 1000);
  return () => clearInterval(id);
}, []);
<span suppressHydrationWarning>{now ? now.toLocaleTimeString() : "—"}</span>
```

### Bundle hygiene

Never import `node:fs`, `node:path`, `node:child_process`, or anything from `lib/portfolio.ts` in a `"use client"` file. Next will surface this as a build error in some cases and silently ship Node polyfills in others. Use `lib/compute.ts` for everything client-safe.

## Accessibility (light)

We're a single-user dashboard; full WCAG isn't the bar. But basic semantics cost nothing and make the IDE / screen-reader experience saner:

- Use semantic HTML: `<button>` over `<div onClick>`, `<table>` with `<th scope="col">`, `<nav>` for nav rails.
- `aria-label` on icon-only buttons (`<button aria-label="Refresh"><RefreshIcon /></button>`).
- Tailwind `focus-visible:` over `focus:` so focus rings only show on keyboard nav, not mouse clicks.
- Color contrast is fine in our zinc palette by default — don't drop below `text-zinc-400` on `bg-zinc-900` for body text.

## Adding a new chart / widget — checklist

1. **Derive data** in [lib/compute.ts](../lib/compute.ts). Pure function, takes `Portfolio` (or a subset), returns serializable.
2. **Render** in `components/X.tsx`. Props are the derived data + any callbacks. No fetching inside.
3. **Mount** in `Dashboard.tsx` inside an existing `<Card>`, with `useMemo` over the derivation.
4. **Reference** the [add-dashboard-card skill](../.claude/skills/add-dashboard-card/SKILL.md) — full template + paste targets.

If the widget needs a new derived metric that mirrors a Python-side stat, mirror the function name (`computeHitStats` ↔ `compute_hit_stats`) so the parity is greppable.

## Adding a new API route — checklist

1. **File**: `app/api/<name>/route.ts` with `export const dynamic = "force-dynamic"`.
2. **Read** through [lib/portfolio.ts](../lib/portfolio.ts) — never re-implement fs.
3. **Path-allowlist** any user-provided basename via `Set<string>` (template: [app/api/logs/route.ts](../app/api/logs/route.ts)).
4. **Reference**: [add-readonly-endpoint skill](../.claude/skills/add-readonly-endpoint/SKILL.md).
5. **Test**: [verify-web skill](../.claude/skills/verify-web/SKILL.md) covers the curl recipe.

## Common pitfalls

### JSON serialization across the SC→CC boundary

These don't survive:

| Type | Symptom | Fix |
|---|---|---|
| `Date` | becomes string in props, deserialize lost | stringify on server, parse on client |
| `Map` / `Set` | becomes empty `{}` / `[]` | convert to array/object on server |
| Functions | Next throws in dev, silently drops in prod | move to client component or call from route handler |
| `BigInt` | throws "not serializable" | convert to `string` |

Pattern: keep server→client props as plain JSON-shaped data. `Portfolio` deliberately stores ISO strings, not `Date` objects, for this reason.

### Hydration mismatch — extended root causes

Beyond the table earlier in this doc:

- Any `if (typeof window !== "undefined")` branch in render — server returns one HTML, client renders another, mismatch.
- Browser extensions injecting attributes on `<body>` (Grammarly, Dark Reader). Suppress with `<body suppressHydrationWarning>`.
- Time-zone-dependent formatting (`toLocaleString` without explicit `locale`/`timeZone`) — the server uses UTC, browser uses local.
- Conditional rendering based on `localStorage` — empty on server, populated on client.

### Passing huge props server→client

`portfolio.closed_trades` with 500+ entries crosses the boundary fine, but Next will warn at >100kB serialized. If a new prop bloats the payload (e.g. full `claude_calls.jsonl` history), split: keep heavy data behind a route handler the client fetches on-demand.

### Recharts re-mount loop

Symptom: chart flickers every poll, fonts jump. Cause: a `key` or `data` prop with a new identity each render.

Fix: round / quantize the value before keying. See `liveEquityRounded` precedent.

### `"use client"` cascade

`"use client"` on `Component.tsx` doesn't just mark that file — every module it imports also ships to the client. So importing `lib/portfolio.ts` (which uses `node:fs`) from a client component breaks the build. Test it: `npm run dev` and check the console.

The mental model: `"use client"` is the boundary of the client bundle, and the bundle's transitive imports must all be browser-safe.
