import { promises as fs } from "node:fs";
import path from "node:path";
import type { GateBlock, Portfolio } from "./types";

const PORTFOLIO_PATH = path.join(process.cwd(), "..", "portfolio.json");
const GATE_LOG_PATH = path.join(process.cwd(), "..", "gate_blocks.jsonl");
const BACKTEST_PATH = path.join(process.cwd(), "..", "backtest_report.json");

export async function readPortfolio(): Promise<Portfolio> {
  const raw = await fs.readFile(PORTFOLIO_PATH, "utf8");
  const data = JSON.parse(raw);
  return {
    open_trades: data.open_trades ?? [],
    closed_trades: data.closed_trades ?? [],
    watch_levels: data.watch_levels ?? [],
    pending_recommendations: data.pending_recommendations ?? [],
    cash_eur: data.cash_eur ?? 0,
    total_capital_eur: data.total_capital_eur ?? 0,
    last_analysis: data.last_analysis,
    last_updated: data.last_updated,
    notes: data.notes,
    kill_switch_active: data.kill_switch_active ?? data.kill_switch,
    kill_switch: data.kill_switch,
    kill_switch_reason: data.kill_switch_reason,
    kill_switch_ts: data.kill_switch_ts,
    dd_halt_active: data.dd_halt_active,
    heartbeat: data.heartbeat,
    correlation_matrix: data.correlation_matrix,
  };
}

export async function readGateBlocks(limit = 500): Promise<GateBlock[]> {
  try {
    const raw = await fs.readFile(GATE_LOG_PATH, "utf8");
    const lines = raw.split("\n").filter((l) => l.trim().length > 0);
    const slice = lines.slice(-limit);
    const out: GateBlock[] = [];
    for (const ln of slice) {
      try {
        out.push(JSON.parse(ln) as GateBlock);
      } catch {
        // skip malformed lines
      }
    }
    return out;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
}

export async function readBacktestReport(): Promise<unknown | null> {
  try {
    const raw = await fs.readFile(BACKTEST_PATH, "utf8");
    return JSON.parse(raw);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw err;
  }
}

export {
  computeEquityCurve,
  computeHitStats,
  currentEquity,
  openExposure,
} from "./compute";
