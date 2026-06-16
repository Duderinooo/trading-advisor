import { NextResponse } from "next/server";
import { enqueueWebCommand } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * The dashboard's only write endpoint. Accepts a proposed-trade action and
 * enqueues it into web_commands; the bot drains + executes under its lock.
 * Never writes portfolio state directly (invariant #1). Validates strictly —
 * unknown action / bad ticker / non-positive numbers are rejected.
 */
export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const b = (body ?? {}) as Record<string, unknown>;
  const action = b.action;
  const ticker = b.ticker;

  if (action !== "fire" && action !== "cancel") {
    return NextResponse.json({ error: "action must be fire|cancel" }, { status: 400 });
  }
  if (typeof ticker !== "string" || !/^[A-Z0-9.]{1,12}$/i.test(ticker)) {
    return NextResponse.json({ error: "bad ticker" }, { status: 400 });
  }

  const payload: Record<string, number> = {};
  if (action === "fire") {
    if (b.entry_price != null) {
      const e = Number(b.entry_price);
      if (!Number.isFinite(e) || e <= 0) {
        return NextResponse.json({ error: "bad entry_price" }, { status: 400 });
      }
      payload.entry_price = e;
    }
    if (b.shares != null) {
      const s = Number(b.shares);
      if (!Number.isInteger(s) || s < 1) {
        return NextResponse.json({ error: "bad shares" }, { status: 400 });
      }
      payload.shares = s;
    }
  }

  try {
    const id = enqueueWebCommand(action, ticker, payload);
    return NextResponse.json({ ok: true, id });
  } catch (e) {
    // Table missing (bot never ran init_schema) or DB locked.
    return NextResponse.json({ error: `queue unavailable: ${e}` }, { status: 503 });
  }
}
