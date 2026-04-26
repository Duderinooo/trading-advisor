import { readBacktestReport } from "@/lib/portfolio";

export const dynamic = "force-dynamic";

export async function GET() {
  const rep = await readBacktestReport();
  return Response.json(rep);
}
