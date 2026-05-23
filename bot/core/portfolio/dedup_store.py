"""Dedup state — persisted in SQLite (state/bot.db).

History:
- Originally inside portfolio.json (seen_news / triggered_events /
  triggered_price_alerts / geo_news_fired).
- Phase E6 extracted to state/dedup.json.
- Phase E7 (2026-05-23): migrated to four SQLite tables. Public API
  unchanged: load_dedup → dict shaped like the old payload; save_dedup →
  full replace; update_dedup → partial merge.

Splitting into four tables lets us do TTL-cleanup as targeted DELETEs
later (DELETE FROM triggered_events WHERE date < ?) instead of loading
the full state into Python and rewriting it — but the immediate goal of
this commit is the backend swap, so the API is preserved verbatim.
"""

import json
import logging
import threading
from pathlib import Path

from core.db import connect, init_schema


logger = logging.getLogger(__name__)


_LEGACY_JSON_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / "dedup.json"
)
_DEDUP_LOCK = threading.RLock()
_DEDUP_KEYS = (
    "seen_news",
    "triggered_events",
    "triggered_price_alerts",
    "geo_news_fired",
)
_MIGRATED = False


def _empty_state() -> dict:
    return {
        "seen_news": {},
        "triggered_events": [],
        "triggered_price_alerts": [],
        "geo_news_fired": {},
    }


def _migrate_from_legacy_if_needed() -> None:
    """One-shot: import from state/dedup.json or portfolio.json fallback."""
    global _MIGRATED
    if _MIGRATED:
        return
    init_schema()
    with connect() as conn:
        # Any table already populated → skip migration
        for tbl in (
            "seen_news",
            "triggered_events",
            "triggered_price_alerts",
            "geo_news_fired",
        ):
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()
            if row["n"] > 0:
                _MIGRATED = True
                return

        legacy = _empty_state()
        if _LEGACY_JSON_PATH.exists():
            try:
                with _LEGACY_JSON_PATH.open() as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k in _DEDUP_KEYS:
                        if k in data:
                            legacy[k] = data[k]
            except Exception:
                logger.exception("Reading legacy dedup.json failed")
        else:
            try:
                from core.portfolio.io import _load_portfolio_raw
                pf = _load_portfolio_raw()
                for k in _DEDUP_KEYS:
                    v = pf.get(k)
                    if v is not None:
                        legacy[k] = v
            except Exception:
                logger.exception("Fallback portfolio.json read failed")

        _write_full(conn, legacy)
        logger.info(
            "dedup migration: imported from %s",
            "dedup.json" if _LEGACY_JSON_PATH.exists() else "portfolio.json",
        )
        if _LEGACY_JSON_PATH.exists():
            try:
                _LEGACY_JSON_PATH.rename(
                    _LEGACY_JSON_PATH.with_suffix(".json.migrated")
                )
            except Exception:
                logger.exception("Renaming legacy dedup.json after migration failed")
    _MIGRATED = True


def _write_full(conn, state: dict) -> None:
    """Replace all four tables atomically inside the given connection."""
    conn.execute("DELETE FROM seen_news")
    conn.execute("DELETE FROM triggered_events")
    conn.execute("DELETE FROM triggered_price_alerts")
    conn.execute("DELETE FROM geo_news_fired")

    seen = state.get("seen_news") or {}
    rows = []
    for date, hashes in seen.items():
        if not isinstance(hashes, (list, tuple, set)):
            continue
        for h in hashes:
            rows.append((str(date), str(h)))
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_news (date, hash) VALUES (?, ?)", rows
        )

    events = state.get("triggered_events") or []
    if events:
        conn.executemany(
            "INSERT OR REPLACE INTO triggered_events (key, date, ts, time) "
            "VALUES (?, ?, ?, ?)",
            [
                (
                    str(e.get("key", "")),
                    str(e.get("date", "")),
                    e.get("ts"),
                    e.get("time"),
                )
                for e in events
                if isinstance(e, dict)
            ],
        )

    alerts = state.get("triggered_price_alerts") or []
    if alerts:
        conn.executemany(
            "INSERT OR IGNORE INTO triggered_price_alerts (key, date) VALUES (?, ?)",
            [
                (str(a.get("key", "")), str(a.get("date", "")))
                for a in alerts
                if isinstance(a, dict)
            ],
        )

    geo = state.get("geo_news_fired") or {}
    if geo:
        conn.executemany(
            "INSERT OR REPLACE INTO geo_news_fired (comm_key, ts) VALUES (?, ?)",
            [(str(k), str(v)) for k, v in geo.items()],
        )


def _read_full(conn) -> dict:
    out = _empty_state()

    seen: dict[str, list[str]] = {}
    for r in conn.execute("SELECT date, hash FROM seen_news"):
        seen.setdefault(r["date"], []).append(r["hash"])
    out["seen_news"] = seen

    out["triggered_events"] = [
        {"key": r["key"], "date": r["date"], "ts": r["ts"], "time": r["time"]}
        for r in conn.execute(
            "SELECT key, date, ts, time FROM triggered_events ORDER BY ts ASC"
        )
    ]

    out["triggered_price_alerts"] = [
        {"key": r["key"], "date": r["date"]}
        for r in conn.execute(
            "SELECT key, date FROM triggered_price_alerts"
        )
    ]

    out["geo_news_fired"] = {
        r["comm_key"]: r["ts"]
        for r in conn.execute("SELECT comm_key, ts FROM geo_news_fired")
    }

    return out


def load_dedup() -> dict:
    """Return the full dedup state (same shape as the old dict)."""
    with _DEDUP_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            return _read_full(conn)


def save_dedup(state: dict) -> None:
    """Replace the full dedup state. Called after caller mutated the dict
    obtained from load_dedup()."""
    with _DEDUP_LOCK:
        _migrate_from_legacy_if_needed()
        with connect() as conn:
            _write_full(conn, state)


def update_dedup(updates: dict) -> None:
    """Partial update: load + merge top-level keys + save."""
    with _DEDUP_LOCK:
        state = load_dedup()
        state.update(updates)
        save_dedup(state)
