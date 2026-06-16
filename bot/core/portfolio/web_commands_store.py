"""Web-command queue — the read-only dashboard's write channel (SQLite, table
web_commands).

The web app must never write portfolio.json (it races the bot's portfolio_lock
= corruption). Instead the Accept/Cancel/Fire buttons enqueue a command here;
the bot's main loop drains pending commands and executes them under the lock.

action ∈ {fire, cancel}; payload is a JSON dict of overrides (entry_price,
shares); status ∈ {pending, done, error}. Both the web (better-sqlite3) and the
bot (python sqlite3) touch this table — SQLite's own file locking covers the
cross-process access; this table is independent of portfolio.json.
"""

import json
import logging
import threading
from datetime import datetime

from core.db import connect, init_schema


logger = logging.getLogger(__name__)

_LOCK = threading.RLock()


def enqueue_command(action: str, ticker: str, payload: dict | None = None) -> int:
    """Append a pending command. Returns its row id. (Bot-side helper; the web
    writes the same row shape directly via better-sqlite3.)"""
    with _LOCK:
        init_schema()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO web_commands (created_at, action, ticker, payload, status) "
                "VALUES (?, ?, ?, ?, 'pending')",
                (now, action, ticker.upper(), json.dumps(payload or {})),
            )
            return int(cur.lastrowid)


def pending_commands() -> list[dict]:
    """All pending commands in insertion order."""
    with _LOCK:
        init_schema()
        with connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, action, ticker, payload FROM web_commands "
                "WHERE status='pending' ORDER BY id ASC"
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            try:
                payload = json.loads(r["payload"]) if r["payload"] else {}
            except json.JSONDecodeError:
                payload = {}
            out.append({
                "id": r["id"], "created_at": r["created_at"],
                "action": r["action"], "ticker": r["ticker"], "payload": payload,
            })
        return out


def mark_command(cmd_id: int, status: str, result: str = "") -> None:
    """Mark a command done/error with a short result string."""
    with _LOCK:
        init_schema()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with connect() as conn:
            conn.execute(
                "UPDATE web_commands SET status=?, result=?, processed_at=? WHERE id=?",
                (status, result[:500], now, cmd_id),
            )
