import { readSystemPrompt } from "@/lib/portfolio";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const hash = url.searchParams.get("hash") ?? "";
  if (!hash) {
    return Response.json({ error: "hash query param required" }, { status: 400 });
  }
  const body = await readSystemPrompt(hash);
  if (body === null) {
    return Response.json({ error: "not found" }, { status: 404 });
  }
  return Response.json({ hash, body });
}
