import Database from "better-sqlite3";
import path from "node:path";

/**
 * Read-only connection to the bot's SQLite store. The bot is the sole
 * writer (see ../bot/CLAUDE.md — `core.portfolio.portfolio_lock`); the
 * dashboard must never write. Opening in readonly mode enforces that at
 * the SQLite level, so a mistaken UPDATE/INSERT from a route handler
 * will throw rather than corrupt state.
 *
 * Connections are cached per process because better-sqlite3 connections
 * are cheap to open but file descriptors aren't unlimited, and Next.js
 * keeps server-component handlers alive between requests. WAL mode on
 * the writer side lets us read while the bot is writing, so this single
 * cached connection is enough.
 */

const DB_PATH = path.join(process.cwd(), "..", "bot", "state", "bot.db");

let _db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (_db) return _db;
  _db = new Database(DB_PATH, { readonly: true, fileMustExist: false });
  _db.pragma("journal_mode = WAL");
  _db.pragma("query_only = ON");
  return _db;
}

export function closeDb(): void {
  if (_db) {
    _db.close();
    _db = null;
  }
}

/**
 * THE one sanctioned write path. The dashboard never writes portfolio.json
 * (lock race = corruption — invariant #1), but the Accept/Cancel/Fire buttons
 * need a write channel: they enqueue a command into web_commands, which the
 * bot drains under portfolio_lock. This connection is writable but only ever
 * touches the web_commands table — never portfolio state. SQLite WAL covers
 * the cross-process access with the Python bot.
 */
let _writeDb: Database.Database | null = null;

function getWriteDb(): Database.Database {
  if (_writeDb) return _writeDb;
  _writeDb = new Database(DB_PATH, { fileMustExist: true });
  _writeDb.pragma("journal_mode = WAL");
  _writeDb.pragma("busy_timeout = 3000");
  return _writeDb;
}

export function enqueueWebCommand(
  action: "fire" | "cancel",
  ticker: string,
  payload: Record<string, number>,
): number {
  const db = getWriteDb();
  const now = new Date().toISOString().slice(0, 19).replace("T", " ");
  const info = db
    .prepare(
      "INSERT INTO web_commands (created_at, action, ticker, payload, status) " +
        "VALUES (?, ?, ?, ?, 'pending')",
    )
    .run(now, action, ticker.toUpperCase(), JSON.stringify(payload));
  return Number(info.lastInsertRowid);
}

/** Test whether the DB file exists yet — bot creates it lazily on first
 * write, so during fresh-clone / pre-first-run the file is missing. */
export function dbExists(): boolean {
  try {
    const fs = require("node:fs");
    return fs.existsSync(DB_PATH);
  } catch {
    return false;
  }
}
