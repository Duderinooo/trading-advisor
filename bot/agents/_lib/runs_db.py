"""SQLite invoke-log for agent runs. Mirrors the pattern from the dev-pipeline
project: every CLI invocation is logged for audit, latency tracking, and
quality regression analysis.

Schema is minimal — runs_db is for observability, not state. Real state stays
in bot.db (portfolio, traces, etc).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parents[2] / "state" / "agent_runs.db"
_LOCK = threading.RLock()
_INIT = False


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_runs_db() -> None:
    """Create schema on first call. Cheap idempotent."""
    global _INIT
    if _INIT:
        return
    with _LOCK:
        if _INIT:
            return
        with _connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    agent TEXT NOT NULL,
                    mode TEXT,
                    model TEXT,
                    duration_ms INTEGER,
                    exit_code INTEGER,
                    output_size INTEGER,
                    error TEXT,
                    meta_json TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_agent_ts ON runs(agent, ts DESC)")
        _INIT = True


def log_run(
    agent: str, *, mode: str | None = None, model: str | None = None,
    duration_ms: int | None = None, exit_code: int | None = None,
    output_size: int | None = None, error: str | None = None,
    meta: dict | None = None,
) -> None:
    init_runs_db()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO runs (ts,agent,mode,model,duration_ms,exit_code,output_size,error,meta_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (time.time(), agent, mode, model, duration_ms, exit_code,
             output_size, error, json.dumps(meta) if meta else None),
        )


def recent_runs(agent: str | None = None, limit: int = 50) -> list[dict]:
    init_runs_db()
    with _LOCK, _connect() as conn:
        conn.row_factory = sqlite3.Row
        if agent:
            rows = conn.execute(
                "SELECT * FROM runs WHERE agent=? ORDER BY ts DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY ts DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
