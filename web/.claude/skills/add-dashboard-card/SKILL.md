---
name: add-dashboard-card
description: Add a new widget/card to the trading dashboard, wired into Dashboard.tsx. Use when the user asks to add a card, widget, panel, KPI, chart, or new UI section to the dashboard. Triggers on "neue card", "add widget", "neuer chart", "add panel", "show X on dashboard", "neue KPI", "dashboard erweitern".
---

# Add a dashboard card

Goal: add a new section (Card, KPI, chart, table) to the dashboard at [../../components/Dashboard.tsx](../../components/Dashboard.tsx).

## Pre-flight

1. Read [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) and [../../docs/CONVENTIONS.md](../../docs/CONVENTIONS.md).
2. Decide: server-renderable component (no hooks/events) or client component (`"use client"`).
3. If the card needs new derived data, **add the function to [../../lib/compute.ts](../../lib/compute.ts) first** (must be pure, isomorphic, no fs).
4. If the data already exists on the `Portfolio` type, just consume it via props.

## Template — server-renderable widget

```tsx
// components/<Name>.tsx
import type { Portfolio } from "@/lib/types";

export default function MyWidget({ portfolio }: { portfolio: Portfolio }) {
  // pure render, no hooks
  return (
    <div className="text-sm text-zinc-300">
      {/* ... */}
    </div>
  );
}
```

## Template — client widget with hooks

```tsx
// components/<Name>.tsx
"use client";

import { useMemo } from "react";
import type { Portfolio } from "@/lib/types";
import { computeXxx } from "@/lib/compute";

export default function MyWidget({ portfolio }: { portfolio: Portfolio }) {
  const derived = useMemo(() => computeXxx(portfolio), [portfolio]);
  return <div>{/* ... */}</div>;
}
```

Don't fetch inside the widget. The parent Dashboard already polls; receive data via props.

## Wire into Dashboard

In [../../components/Dashboard.tsx](../../components/Dashboard.tsx):

```tsx
import MyWidget from "@/components/MyWidget";

// inside <main>...
<Card title="My Section">
  <MyWidget portfolio={portfolio} />
</Card>
```

For a metric tile, use `<Kpi label value sub tone />` from [../../components/Card.tsx](../../components/Card.tsx) — already grids 4 across at md+.

## Rules

- Use existing primitives: `<Card>`, `<Kpi>`. Don't invent new shells unless layout legitimately differs.
- Tone palette: `zinc` surfaces, `emerald` good, `rose` bad, `amber` warn. Stay in palette.
- Wrap derived computations in `useMemo` keyed on the relevant slice of `portfolio` — polling causes re-renders every 30 s.
- If the widget owns its own filter/toggle state (like [EquityChart.tsx](../../components/EquityChart.tsx) timeframe), keep that state inside the widget. Don't lift to Dashboard.
- For tables, follow [../../components/OpenTrades.tsx](../../components/OpenTrades.tsx) markup pattern: `<table className="w-full text-sm">` with `border-b border-zinc-900/60`.
- For charts, Recharts is the only allowed library. Match the styling in [../../components/EquityChart.tsx](../../components/EquityChart.tsx) (zinc grid, geist font sizes, dark tooltip).

## Verify

```bash
npx tsc --noEmit
npm run dev > /tmp/web.log 2>&1 &
sleep 4
curl -s -o /tmp/p.html -w "%{http_code}\n" http://localhost:3000/
grep -o "My Section" /tmp/p.html   # confirm rendered
pkill -f "next dev"
```

Then open the page in a browser and check the console for hydration warnings — `curl` does not surface those.
