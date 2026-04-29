import { readClaudeCalls } from "@/lib/portfolio";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const limitParam = url.searchParams.get("limit") ?? "200";
  const limit = Math.min(2000, Math.max(20, parseInt(limitParam, 10) || 200));
  const mode = url.searchParams.get("mode");

  let calls = await readClaudeCalls(limit);
  if (mode) calls = calls.filter((c) => c.mode === mode);

  return Response.json({ calls });
}
