import { promises as fs } from "node:fs";
import path from "node:path";
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const ALLOWED = new Set(["bot.log", "bot.err"]);

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const file = url.searchParams.get("file") ?? "bot.log";
  const linesParam = url.searchParams.get("lines") ?? "300";
  const lines = Math.min(2000, Math.max(10, parseInt(linesParam, 10) || 300));

  if (!ALLOWED.has(file)) {
    return Response.json({ error: "invalid file" }, { status: 400 });
  }

  const fullPath = path.join(process.cwd(), "..", file);
  try {
    const stat = await fs.stat(fullPath);
    const content = await fs.readFile(fullPath, "utf8");
    const all = content.split("\n");
    const tail = all.slice(-lines).join("\n");
    return Response.json({
      file,
      size: stat.size,
      mtime: stat.mtime.toISOString(),
      lines: all.length,
      tail,
    });
  } catch (e) {
    return Response.json(
      { error: `read failed: ${(e as Error).message}` },
      { status: 500 },
    );
  }
}
