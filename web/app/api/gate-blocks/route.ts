import { readGateBlocks } from "@/lib/portfolio";
import { aggregateGateBlocks } from "@/lib/compute";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const limitParam = url.searchParams.get("limit") ?? "500";
  const limit = Math.min(2000, Math.max(50, parseInt(limitParam, 10) || 500));

  const blocks = await readGateBlocks(limit);
  const summary = aggregateGateBlocks(blocks);
  return Response.json({ blocks, summary });
}
