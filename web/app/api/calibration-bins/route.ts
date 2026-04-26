import { readPortfolio } from "@/lib/portfolio";
import { computeCalibrationBins } from "@/lib/compute";

export const dynamic = "force-dynamic";

export async function GET() {
  const p = await readPortfolio();
  return Response.json(computeCalibrationBins(p.closed_trades));
}
