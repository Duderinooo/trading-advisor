import { readKv } from "@/lib/portfolio";

export const dynamic = "force-dynamic";

/**
 * Read the bot-precomputed earnings calendar from
 * kv_state(namespace='calendar', key='earnings'). The bot refreshes
 * this row once per day at EOD and again during morning prep, so the
 * dashboard never has to hit yfinance directly.
 */
export async function GET() {
  const payload = readKv("calendar", "earnings") as
    | {
        generated_at?: string;
        days_ahead?: number;
        events?: Array<{
          ticker: string;
          earnings_date: string;
          days_until: number;
        }>;
      }
    | undefined;
  if (!payload) {
    return Response.json({ generated_at: null, events: [] });
  }
  return Response.json({
    generated_at: payload.generated_at ?? null,
    days_ahead: payload.days_ahead ?? null,
    events: payload.events ?? [],
  });
}
