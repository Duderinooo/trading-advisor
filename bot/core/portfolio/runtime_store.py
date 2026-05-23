"""Runtime / ephemeral state — persisted in SQLite (state/bot.db,
kv_state table with namespace='runtime').

History:
- Originally inside portfolio.json: heartbeat, entry_gate_cooldowns,
  transient_retries, correlation_matrix.
- Phase E6 extracted to state/runtime.json (own lock, atomic writes).
- Phase E7 (2026-05-23): migrated to SQLite kv_state. API unchanged.

The heartbeat key alone is mutated every 60s by the main loop, which is
the main reason this state was split out of portfolio.json originally —
SQLite WAL keeps that high write rate from blocking reads elsewhere.
"""

import json
import logging
import threading
from pathlib import Path

from core.db import connect, init_schema


logger = logging.getLogger(__name__)


_LEGACY_JSON_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / "runtime.json"
)
_NAMESPACE = "runtime"
_RUNTIME_LOCK = threading.RLock()
_RUNTIME_KEYS = (
    "heartbeat",
    "entry_gate_cooldowns",
    "transient_retries",
    "correlation_matrix",
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
                logger.exception("Reading legacy runtime.json failed")
        else:
            try:
                from core.portfolio.io import _load_portfolio_raw
                pf = _load_portfolio_raw()
                legacy = {k: pf[k] for k in _RUNTIME_KEYS if k in pf}
            except Exception:
                logger.exception("Fallback portfolio.json read failed")

        if legacy:
            conn.executemany(
                "INSERT OR REPLACE INTO kv_state (namespace, key, body) "
                "VALUES (?, ?, ?)",
                [(_NAMESPACE, k, json.dumps(v)) for k, v in legacy.items()],
            )
            logger.info(
                "runtime migration: imported %d keys from %s",
                len(legacy),
                "runtime.json" if _LEGACY_JSON_PATH.exists() else "portfolio.json",
            )
            if _LEGACY_JSON_PATH.exists():
                try:
                    _LEGACY_JSON_PATH.rename(
                        _LEGACY_JSON_PATH.with_suffix(".json.migrated")
                    )
                except Exception:
                    logger.exception("Renaming legacy runtime.json after migration failed")
    _MIGRATED = True


def load_runtime() -> dict:
    """Return full runtime dict (key → blob value). Missing keys absent."""
    with _RUNTIME_LOCK:
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
                logger.warning("Skipping malformed runtime row: %s", r["key"])
        return out


def save_runtime(state: dict) -> None:
    """Replace the full runtime namespace with `state`."""
    with _RUNTIME_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            conn.execute(
                "DELETE FROM kv_state WHERE namespace=?", (_NAMESPACE,)
            )
            if state:
                conn.executemany(
                    "INSERT INTO kv_state (namespace, key, body) VALUES (?, ?, ?)",
                    [(_NAMESPACE, k, json.dumps(v)) for k, v in state.items()],
                )


def update_runtime(updates: dict) -> None:
    """Partial update: upsert only the keys in `updates`, leave others."""
    with _RUNTIME_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO kv_state (namespace, key, body) "
                "VALUES (?, ?, ?)",
                [(_NAMESPACE, k, json.dumps(v)) for k, v in updates.items()],
            )
