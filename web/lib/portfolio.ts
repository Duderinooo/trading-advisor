import { promises as fs } from "node:fs";
import path from "node:path";
import { dbExists, getDb } from "./db";
import type { ClaudeCall, GateBlock, PaperPortfolio, Portfolio } from "./types";

/**
 * Paths follow the 2026-05-23 bot/ + web/ split: web is `<repo>/web`,
 * bot files are under `<repo>/bot`. Resolved relative to `process.cwd()`
 * which Next.js anchors at the web/ directory during `next dev` and
 * `next build`.
 */
const BOT_DIR = path.join(process.cwd(), "..", "bot");
const PORTFOLIO_PATH = path.join(BOT_DIR, "portfolio.json");
const PAPER_PORTFOLIO_PATH = path.join(BOT_DIR, "training_portfolio.json");
const GATE_LOG_PATH = path.join(BOT_DIR, "gate_blocks.jsonl");
const BACKTEST_PATH = path.join(BOT_DIR, "backtest_report.json");
const CALLS_LOG_PATH = path.join(BOT_DIR, "claude_calls.jsonl");
const PROMPT_DIR = path.join(BOT_DIR, ".system_prompts");

/**
 * State that used to live in portfolio.json — closed_trades,
 * pending_recommendations, heartbeat, correlation_matrix, the per-mode
 * traces — was migrated to SQLite (state/bot.db) by the bot's Phase E7
 * refactor. The dashboard joins both sources here so the rest of the
 * codebase still sees one Portfolio shape.
 */

function readClosedTrades(): unknown[] {
  if (!dbExists()) return [];
  const rows = getDb()
    .prepare("SELECT body FROM closed_trades ORDER BY id ASC")
    .all() as { body: string }[];
  const out: unknown[] = [];
  for (const r of rows) {
    try {
      out.push(JSON.parse(r.body));
    } catch {
      // skip malformed rows — same tolerance as the Python loader
    }
  }
  return out;
}

function readPending(): unknown[] {
  if (!dbExists()) return [];
  const rows = getDb()
    .prepare("SELECT body FROM pending_recommendations ORDER BY id ASC")
    .all() as { body: string }[];
  const out: unknown[] = [];
  for (const r of rows) {
    try {
      out.push(JSON.parse(r.body));
    } catch {}
  }
  return out;
}

function readKv(namespace: string, key: string): unknown {
  if (!dbExists()) return undefined;
  const row = getDb()
    .prepare("SELECT body FROM kv_state WHERE namespace = ? AND key = ?")
    .get(namespace, key) as { body: string } | undefined;
  if (!row) return undefined;
  try {
    return JSON.parse(row.body);
  } catch {
    return undefined;
  }
}

export async function readPortfolio(): Promise<Portfolio> {
  const raw = await fs.readFile(PORTFOLIO_PATH, "utf8");
  const data = JSON.parse(raw);
  const closed = readClosedTrades();
  const pending = readPending();
  const heartbeat = readKv("runtime", "heartbeat");
  const correlation_matrix = readKv("runtime", "correlation_matrix");
  const last_morning_trace = readKv("trace", "last_morning_trace");
  const last_event_trace = readKv("trace", "last_event_trace");
  const last_opening_trace_xetra = readKv("trace", "last_opening_trace_xetra");
  const last_opening_trace_us = readKv("trace", "last_opening_trace_us");
  return {
    open_trades: data.open_trades ?? [],
    closed_trades: closed as Portfolio["closed_trades"],
    watch_levels: data.watch_levels ?? [],
    pending_recommendations: pending as Portfolio["pending_recommendations"],
    cash_movements: data.cash_movements ?? [],
    equity_history: data.equity_history ?? [],
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
    heartbeat: heartbeat as Portfolio["heartbeat"],
    correlation_matrix: correlation_matrix as Portfolio["correlation_matrix"],
    last_morning_trace: last_morning_trace as Portfolio["last_morning_trace"],
    last_event_trace: last_event_trace as Portfolio["last_event_trace"],
    last_opening_trace_xetra: last_opening_trace_xetra as Portfolio["last_opening_trace_xetra"],
    last_opening_trace_us: last_opening_trace_us as Portfolio["last_opening_trace_us"],
  };
}

export async function readPaperPortfolio(): Promise<PaperPortfolio | null> {
  // Phase E7 follow-up: paper portfolio migrated from
  // training_portfolio.json to SQLite kv_state(namespace='paper').
  // Falls back to the legacy file for pre-migration installs.
  const fromDb = readKv("paper", "portfolio") as
    | Partial<PaperPortfolio>
    | undefined;
  if (fromDb) {
    return {
      open_trades: fromDb.open_trades ?? [],
      closed_trades: fromDb.closed_trades ?? [],
      cash_eur: fromDb.cash_eur ?? 0,
      total_capital_eur: fromDb.total_capital_eur ?? 0,
      started_at: fromDb.started_at,
      paper: true,
      last_updated: fromDb.last_updated,
    };
  }
  try {
    const raw = await fs.readFile(PAPER_PORTFOLIO_PATH, "utf8");
    const data = JSON.parse(raw);
    return {
      open_trades: data.open_trades ?? [],
      closed_trades: data.closed_trades ?? [],
      cash_eur: data.cash_eur ?? 0,
      total_capital_eur: data.total_capital_eur ?? 0,
      started_at: data.started_at,
      paper: true,
      last_updated: data.last_updated,
    };
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw err;
  }
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

export async function readClaudeCalls(limit = 200): Promise<ClaudeCall[]> {
  try {
    const raw = await fs.readFile(CALLS_LOG_PATH, "utf8");
    const lines = raw.split("\n").filter((l) => l.trim().length > 0);
    const slice = lines.slice(-limit);
    const out: ClaudeCall[] = [];
    for (const ln of slice) {
      try {
        out.push(JSON.parse(ln) as ClaudeCall);
      } catch {
        // skip malformed
      }
    }
    return out;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
}

export async function readSystemPrompt(hash: string): Promise<string | null> {
  // Path-traversal guard: hash must be hex, no slashes/dots
  if (!/^[a-f0-9]{6,64}$/.test(hash)) return null;
  try {
    return await fs.readFile(path.join(PROMPT_DIR, `${hash}.txt`), "utf8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return null;
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
