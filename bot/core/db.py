"""SQLite infrastructure for bot state stores.

Single DB file at state/bot.db. One connection per call (sqlite3 is
thread-safe per-connection only with check_same_thread=False, and we have
the Telegram-listener thread + main loop both writing). WAL mode keeps
readers non-blocking during writes.

Schema migration: tables are created on-demand via `init_schema()`. Each
satellite store imports the relevant table-DDL and calls it before any
read/write. No external migration tool — single-user bot, schema changes
land as ALTER statements alongside the code change.

Locking: SQLite's own file-locking covers cross-process safety; the
existing per-store threading.RLock objects still guard load+mutate+save
sequences inside one process (a read followed by a write must remain
atomic at the application layer).
"""

import sqlite3
import threading
from pathlib import Path


_DB_PATH = Path(__file__).resolve().parent.parent / "state" / "bot.db"
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


def db_path() -> Path:
    return _DB_PATH


class _ConnCtx:
    """Wrap sqlite3.Connection to add CLOSE on __exit__.

    Default sqlite3 `with conn:` commits/rolls back but does NOT close.
    At 256-FD limit (launchd default) this leaked us into EMFILE within
    hours. See incident 2026-05-26 multi-occurrence bug-watcher reports.
    """
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
    def __enter__(self) -> sqlite3.Connection:
        return self._conn
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()
        return False


def connect() -> _ConnCtx:
    """Return a fresh connection wrapped to auto-CLOSE on context-exit.

    Use as `with connect() as conn:` — commits/rollbacks AND closes.
    Calling .close() manually also works (delegates to underlying conn).
    """
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        _DB_PATH,
        check_same_thread=False,
        timeout=10.0,  # wait up to 10s for write lock before erroring
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return _ConnCtx(conn)


def init_schema() -> None:
    """Idempotent — creates all known tables. Safe to call repeatedly.

    Each store could call its own DDL but centralising here means the DB
    file is fully shaped on first connect, even if only one store has been
    touched. Cheap (CREATE IF NOT EXISTS is a no-op when already present).
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with connect() as conn:
            conn.executescript(_SCHEMA_DDL)
        _SCHEMA_READY = True


_SCHEMA_DDL = """
-- closed_trades: one row per closed (or partial-closed) trade.
-- Body kept as JSON blob because trade dicts have ~50 keys with sparse
-- evolution over time; promoting individual fields to columns would
-- force migrations every time the schema drifts. Querying is rare and
-- list-shaped (hit_stats loads all rows, then filters in Python).
CREATE TABLE IF NOT EXISTS closed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    closed_at TEXT,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_closed_trades_ticker ON closed_trades(ticker);
CREATE INDEX IF NOT EXISTS idx_closed_trades_closed_at ON closed_trades(closed_at);

-- pending_recommendations: short-lived recs awaiting /confirm.
CREATE TABLE IF NOT EXISTS pending_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    body TEXT NOT NULL
);

-- dedup: seen-news hashes per date.
CREATE TABLE IF NOT EXISTS seen_news (
    date TEXT NOT NULL,
    hash TEXT NOT NULL,
    PRIMARY KEY (date, hash)
);

-- dedup: triggered watch-events.
CREATE TABLE IF NOT EXISTS triggered_events (
    key TEXT NOT NULL,
    date TEXT NOT NULL,
    ts REAL,
    time TEXT,
    PRIMARY KEY (key, date)
);

-- dedup: price alerts per day.
CREATE TABLE IF NOT EXISTS triggered_price_alerts (
    key TEXT NOT NULL,
    date TEXT NOT NULL,
    PRIMARY KEY (key, date)
);

-- dedup: geo-news cooldown per commodity-set. ts is whatever the caller
-- stored — currently an ISO timestamp string, kept as TEXT to match.
CREATE TABLE IF NOT EXISTS geo_news_fired (
    comm_key TEXT PRIMARY KEY,
    ts TEXT NOT NULL
);

-- kv_state: namespaced key/value blob store for small dict-shaped state
-- (runtime: heartbeat, cooldowns, retries, correlation_matrix; trace:
-- per-mode pipeline traces). Body is a JSON blob. Namespacing keeps the
-- two logical stores in one table without coupling their locks.
CREATE TABLE IF NOT EXISTS kv_state (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
"""
