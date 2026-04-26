import { promises as fs } from "node:fs";
import path from "node:path";
import type { Portfolio } from "./types";

const PORTFOLIO_PATH = path.join(process.cwd(), "..", "portfolio.json");

export async function readPortfolio(): Promise<Portfolio> {
  const raw = await fs.readFile(PORTFOLIO_PATH, "utf8");
  const data = JSON.parse(raw);
  return {
    open_trades: data.open_trades ?? [],
    closed_trades: data.closed_trades ?? [],
    watch_levels: data.watch_levels ?? [],
    cash_eur: data.cash_eur ?? 0,
    total_capital_eur: data.total_capital_eur ?? 0,
    last_analysis: data.last_analysis,
    last_updated: data.last_updated,
    notes: data.notes,
    kill_switch_active: data.kill_switch_active,
    dd_halt_active: data.dd_halt_active,
  };
}

export {
  computeEquityCurve,
  computeHitStats,
  currentEquity,
  openExposure,
} from "./compute";
