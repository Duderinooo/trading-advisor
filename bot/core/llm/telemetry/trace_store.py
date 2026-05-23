"""Trace state — per-mode pipeline trace persisted in SQLite (state/bot.db,
kv_state table with namespace='trace').

History:
- Originally inside portfolio.json as last_morning_trace / last_event_trace /
  last_opening_trace_xetra / last_opening_trace_us.
- Phase E extracted to state/traces.json (own lock).
- Phase E7 (2026-05-23): migrated to SQLite kv_state. API unchanged.
"""

import json
import logging
import threading
from pathlib import Path

from core.db import connect, init_schema


logger = logging.getLogger(__name__)


_LEGACY_JSON_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "state" / "traces.json"
)
_NAMESPACE = "trace"
_TRACES_LOCK = threading.RLock()
_TRACE_KEYS = (
    "last_morning_trace",
    "last_event_trace",
    "last_opening_trace_xetra",
    "last_opening_trace_us",
    "last_opening_trace",  # legacy bucket if event_context not parsed
)
_MIGRATED = False


def _migrate_from_legacy_if_needed() -> None:
    global _MIGRATED
    if _MIGRATED:
        return
    init_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM kv_state WHERE namespace=?",
            (_NAMESPACE,),
        ).fetchone()
        if row["n"] > 0:
            _MIGRATED = True
            return

        legacy: dict = {}
        if _LEGACY_JSON_PATH.exists():
            try:
                with _LEGACY_JSON_PATH.open() as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    legacy = data
            except Exception:
                logger.exception("Reading legacy traces.json failed")
        else:
            try:
                from core.portfolio.io import _load_portfolio_raw
                pf = _load_portfolio_raw()
                legacy = {k: pf[k] for k in _TRACE_KEYS if k in pf}
            except Exception:
                logger.exception("Fallback portfolio.json read failed")

        if legacy:
            conn.executemany(
                "INSERT OR REPLACE INTO kv_state (namespace, key, body) "
                "VALUES (?, ?, ?)",
                [(_NAMESPACE, k, json.dumps(v)) for k, v in legacy.items()],
            )
            logger.info(
                "traces migration: imported %d keys from %s",
                len(legacy),
                "traces.json" if _LEGACY_JSON_PATH.exists() else "portfolio.json",
            )
            if _LEGACY_JSON_PATH.exists():
                try:
                    _LEGACY_JSON_PATH.rename(
                        _LEGACY_JSON_PATH.with_suffix(".json.migrated")
                    )
                except Exception:
                    logger.exception("Renaming legacy traces.json after migration failed")
    _MIGRATED = True


def load_traces() -> dict:
    with _TRACES_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            rows = conn.execute(
                "SELECT key, body FROM kv_state WHERE namespace=?",
                (_NAMESPACE,),
            ).fetchall()
        out: dict = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["body"])
            except json.JSONDecodeError:
                logger.warning("Skipping malformed trace row: %s", r["key"])
        return out


def save_trace(key: str, trace: dict) -> None:
    """Upsert a single trace under `key` (e.g. last_morning_trace)."""
    with _TRACES_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (namespace, key, body) "
                "VALUES (?, ?, ?)",
                (_NAMESPACE, key, json.dumps(trace)),
            )
