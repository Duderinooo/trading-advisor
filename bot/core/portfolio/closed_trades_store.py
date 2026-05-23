"""Closed trades — persisted in SQLite (state/bot.db, table closed_trades).

History:
- Originally lived inside portfolio.json under the `closed_trades` key.
- Phase E4 extracted to state/closed_trades.jsonl (line-per-trade, atomic
  full rewrite).
- Phase E7 (2026-05-23): migrated to SQLite. Same public API
  (load/save/append) so all consumers — including the transparent shim in
  core.portfolio.io — keep working without changes. One-shot migration
  copies any leftover jsonl rows into the table on first read.

Each row stores the trade dict as a JSON blob in `body`, with `ticker`
and `closed_at` promoted to columns for cheap filtering / indexed lookups.
Consumers (hit_stats, heat, risk, sizing, backtest) all want a
`list[dict]` — load_closed_trades reconstructs that shape, so callers are
unaware of the storage swap.
"""

import json
import logging
import threading
from pathlib import Path

from core.db import connect, init_schema


logger = logging.getLogger(__name__)


_LEGACY_JSONL_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / "closed_trades.jsonl"
)
_CLOSED_LOCK = threading.RLock()
_MIGRATED = False


def _migrate_from_legacy_if_needed() -> None:
    """One-shot import from legacy jsonl + portfolio.json fallback.

    Runs once per process. After import the legacy file stays on disk
    (renamed .migrated suffix) — DELETED nothing in case of human
    intervention is needed, but won't be re-read again on subsequent
    startups because we check the table contents first.
    """
    global _MIGRATED
    if _MIGRATED:
        return
    init_schema()
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM closed_trades").fetchone()
        if row["n"] > 0:
            _MIGRATED = True
            return

        rows: list[dict] = []
        if _LEGACY_JSONL_PATH.exists():
            try:
                with _LEGACY_JSONL_PATH.open() as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning(
                                "Skipping malformed closed_trades line: %s", line[:80]
                            )
            except Exception:
                logger.exception("Reading legacy closed_trades.jsonl failed")
        else:
            try:
                from core.portfolio.io import _load_portfolio_raw
                pf = _load_portfolio_raw()
                rows = pf.get("closed_trades") or []
            except Exception:
                logger.exception("Fallback portfolio.json read failed")

        if rows:
            conn.executemany(
                "INSERT INTO closed_trades (ticker, closed_at, body) VALUES (?, ?, ?)",
                [
                    (
                        t.get("ticker") or "",
                        t.get("closed_at") or t.get("close_date") or "",
                        json.dumps(t),
                    )
                    for t in rows
                ],
            )
            logger.info(
                "closed_trades migration: imported %d rows from %s",
                len(rows),
                "jsonl" if _LEGACY_JSONL_PATH.exists() else "portfolio.json",
            )
            if _LEGACY_JSONL_PATH.exists():
                try:
                    _LEGACY_JSONL_PATH.rename(
                        _LEGACY_JSONL_PATH.with_suffix(".jsonl.migrated")
                    )
                except Exception:
                    logger.exception("Renaming legacy jsonl after migration failed")
    _MIGRATED = True


def load_closed_trades() -> list[dict]:
    """Return all closed trades in insertion order (oldest first)."""
    with _CLOSED_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            rows = conn.execute(
                "SELECT body FROM closed_trades ORDER BY id ASC"
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            try:
                out.append(json.loads(r["body"]))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed closed_trades row")
        return out


def save_closed_trades(trades: list[dict]) -> None:
    """Replace the full table with `trades`. Used by the transparent shim
    in save_portfolio for compat with the old list-mutation pattern."""
    with _CLOSED_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            conn.execute("DELETE FROM closed_trades")
            if trades:
                conn.executemany(
                    "INSERT INTO closed_trades (ticker, closed_at, body) "
                    "VALUES (?, ?, ?)",
                    [
                        (
                            t.get("ticker") or "",
                            t.get("closed_at") or t.get("close_date") or "",
                            json.dumps(t),
                        )
                        for t in trades
                    ],
                )


def append_closed_trade(trade: dict) -> None:
    """Insert one trade. True append (no full rewrite)."""
    with _CLOSED_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            conn.execute(
                "INSERT INTO closed_trades (ticker, closed_at, body) VALUES (?, ?, ?)",
                (
                    trade.get("ticker") or "",
                    trade.get("closed_at") or trade.get("close_date") or "",
                    json.dumps(trade),
                ),
            )
