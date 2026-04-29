---
name: add-readonly-endpoint
description: Scaffold a new read-only Next.js 16 API route under app/api/ that reads portfolio.json via lib/portfolio.ts. Use when the user asks to add an API endpoint, JSON route, or expose new derived data. Triggers on "neuer endpoint", "add api route", "expose X as JSON", "expose stats", "neuer api", "/api/...".
---

# Add a read-only API route

Goal: scaffold `app/api/<name>/route.ts` consistent with the existing routes in this project.

## Pre-flight

1. Read [../../CLAUDE.md](../../CLAUDE.md) and [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) if not already in context. Critical: this codebase is **read-only**. Refuse POST/PUT/DELETE handlers unless the user has explicitly asked for write capability AND we have a plan that goes through Telegram (not direct fs writes).
2. Confirm the route name matches what the user asked for. If derived data is needed, prefer adding a function to [../../lib/compute.ts](../../lib/compute.ts) and re-using it.

## Template

```ts
// app/api/<name>/route.ts
import { readPortfolio } from "@/lib/portfolio";
// import { computeXxx } from "@/lib/compute";

export const dynamic = "force-dynamic";

export async function GET() {
  const p = await readPortfolio();
  return Response.json(/* derived value or `p` subset */);
}
```

For routes accepting query params:

```ts
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const param = url.searchParams.get("param") ?? "default";
  // VALIDATE: never trust query strings on file paths.
  // Use a Set<string> allowlist if you're reading a file.
  const p = await readPortfolio();
  return Response.json(/* ... */);
}
```

## Rules

- **Always** export `dynamic = "force-dynamic"`. Otherwise Next.js may statically prerender and freeze the data at build time.
- **Never** import `node:fs` directly in the route — go through [../../lib/portfolio.ts](../../lib/portfolio.ts). Single source of truth for the file path.
- **Never** add write handlers (POST/PUT/PATCH/DELETE) to this app without an explicit go-ahead — the Python bot owns `portfolio.json` under a lock and races will corrupt state.
- If the route reads a file other than `portfolio.json`, use a hard-coded allowlist Set; reject anything else with HTTP 400. See [../../app/api/logs/route.ts](../../app/api/logs/route.ts).
- Re-use compute functions from [../../lib/compute.ts](../../lib/compute.ts) — don't reimplement equity/stats logic in the route.

## Verify

```bash
npx tsc --noEmit
npm run dev > /tmp/web.log 2>&1 &
sleep 4
curl -s http://localhost:3000/api/<name> | head -c 300
pkill -f "next dev"
```

Expect 200 + valid JSON. If 500, check the server log for the stack trace.
