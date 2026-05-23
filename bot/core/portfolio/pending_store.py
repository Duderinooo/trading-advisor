"""Pending recommendations — persisted in SQLite (state/bot.db, table
pending_recommendations).

History:
- Originally inside portfolio.json under `pending_recommendations`.
- Phase E5 extracted to state/pending.json (own lock, atomic write).
- Phase E7 (2026-05-23): migrated to SQLite. API unchanged.

Pending recs are short-lived (≤PENDING_REC_TTL_HOURS) and turn over
quickly — every /confirm, every EOD cleanup, every exit-reminder tick
mutates the list. SQLite gives us the same atomicity as the tmp+rename
JSON path with simpler concurrency semantics across the listener thread
+ main loop.
"""

import json
import logging
import threading
from pathlib import Path

from core.db import connect, init_schema


logger = logging.getLogger(__name__)


_LEGACY_JSON_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / "pending.json"
)
_PENDING_LOCK = threading.RLock()
_MIGRATED = False


def _migrate_from_legacy_if_needed() -> None:
    """One-shot import from legacy pending.json + portfolio.json fallback."""
    global _MIGRATED
    if _MIGRATED:
        return
    init_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM pending_recommendations"
        ).fetchone()
        if row["n"] > 0:
            _MIGRATED = True
            return

        rows: list[dict] = []
        if _LEGACY_JSON_PATH.exists():
            try:
                with _LEGACY_JSON_PATH.open() as f:
                    data = json.load(f)
                if isinstance(data, list):
                    rows = data
            except Exception:
                logger.exception("Reading legacy pending.json failed")
        else:
            try:
                from core.portfolio.io import _load_portfolio_raw
                pf = _load_portfolio_raw()
                rows = pf.get("pending_recommendations") or []
            except Exception:
                logger.exception("Fallback portfolio.json read failed")

        if rows:
            conn.executemany(
                "INSERT INTO pending_recommendations (body) VALUES (?)",
                [(json.dumps(r),) for r in rows],
            )
            logger.info(
                "pending migration: imported %d recs from %s",
                len(rows),
                "pending.json" if _LEGACY_JSON_PATH.exists() else "portfolio.json",
            )
            if _LEGACY_JSON_PATH.exists():
                try:
                    _LEGACY_JSON_PATH.rename(
                        _LEGACY_JSON_PATH.with_suffix(".json.migrated")
                    )
                except Exception:
                    logger.exception("Renaming legacy pending.json after migration failed")
    _MIGRATED = True


def load_pending() -> list[dict]:
    """Return current pending-rec list in insertion order."""
    with _PENDING_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            rows = conn.execute(
                "SELECT body FROM pending_recommendations ORDER BY id ASC"
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            try:
                out.append(json.loads(r["body"]))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed pending row")
        return out


def save_pending(pending: list[dict]) -> None:
    """Replace full pending table. Used by save_portfolio's transparent shim."""
    with _PENDING_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            conn.execute("DELETE FROM pending_recommendations")
            if pending:
                conn.executemany(
                    "INSERT INTO pending_recommendations (body) VALUES (?)",
                    [(json.dumps(r),) for r in pending],
                )


def append_pending(rec: dict) -> None:
    """Add one rec without rewriting the whole table."""
    with _PENDING_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            conn.execute(
                "INSERT INTO pending_recommendations (body) VALUES (?)",
                (json.dumps(rec),),
            )
