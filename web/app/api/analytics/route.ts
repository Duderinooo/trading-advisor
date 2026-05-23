import { readKv } from "@/lib/portfolio";

export const dynamic = "force-dynamic";

/**
 * Dashboard analytics bundle — all four kv_state(namespace='analytics')
 * rows in one fetch so widgets share a single network round-trip on
 * the 30s polling tick. The bot writes these rows during EOD,
 * morning prep, and after every confirm/close so freshness is
 * event-driven rather than depending on dashboard cadence.
 */
export async function GET() {
  const keys = [
    "drawdown_trajectory",
    "hit_rate_trend",
    "time_of_day",
    "shadow_what_if",
  ] as const;
  const out: Record<string, unknown> = {};
  for (const k of keys) {
    const row = readKv("analytics", k) as
      | { generated_at?: string; data?: unknown }
      | undefined;
    out[k] = row ?? null;
  }
  return Response.json(out);
}
