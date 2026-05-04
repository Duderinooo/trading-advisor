import { readPaperPortfolio } from "@/lib/portfolio";

export const dynamic = "force-dynamic";

export async function GET() {
  const p = await readPaperPortfolio();
  if (!p) {
    return Response.json({
      paper: false as const,
      open_trades: [],
      closed_trades: [],
      cash_eur: 0,
      total_capital_eur: 0,
    });
  }
  return Response.json(p);
}
