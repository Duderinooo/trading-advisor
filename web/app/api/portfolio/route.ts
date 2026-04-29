import { readPortfolio } from "@/lib/portfolio";

export const dynamic = "force-dynamic";

export async function GET() {
  const p = await readPortfolio();
  return Response.json(p);
}
