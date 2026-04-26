import { readPortfolio } from "@/lib/portfolio";

export const dynamic = "force-dynamic";

export async function GET() {
  const p = await readPortfolio();
  return Response.json({
    heartbeat: p.heartbeat ?? null,
    kill_switch: p.kill_switch ?? p.kill_switch_active ?? false,
    kill_switch_reason: p.kill_switch_reason ?? null,
    kill_switch_ts: p.kill_switch_ts ?? null,
    dd_halt_active: p.dd_halt_active ?? false,
  });
}
