"""Dedup state — keeps news + watch-event + price-alert dedup keys out of portfolio.json.

Previously portfolio.json carried:
- seen_news: {date: [hash, ...]} — per-day hashes of headlines already classified
- triggered_events: [{key, date, ts, time}] — watch-event TTL-dedup
- triggered_price_alerts: [{key, date}] — per-(direction, ticker) per-day dedup
- geo_news_fired: {comm_key: ts} — 6h dedup per commodity-set

All four are append-mostly bookkeeping; mixing them with cash/positions made
portfolio.json large + write-noisy. Now lives in state/dedup.json, atomic
load/save under its own lock.

Migration: on first read, if state/dedup.json doesn't exist, populate from
portfolio.json (the original location). Subsequent reads come from state/dedup.json.
After migration completes, portfolio.json fields are still kept for one cycle
of safety, then pruned.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path


logger = logging.getLogger(__name__)


_DEDUP_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / "dedup.json"
)
_DEDUP_LOCK = threading.RLock()
_DEDUP_KEYS = ("seen_news", "triggered_events", "triggered_price_alerts", "geo_news_fired")


def _empty_state() -> dict:
    return {
        "seen_news": {},
        "triggered_events": [],
        "triggered_price_alerts": [],
        "geo_news_fired": {},
    }


def _migrate_from_portfolio_if_needed() -> None:
    """One-shot: populate state/dedup.json from portfolio.json if missing."""
    if _DEDUP_PATH.exists():
        return
    try:
        from core.portfolio.io import load_portfolio
        pf = load_portfolio()
        initial = _empty_state()
        for k in _DEDUP_KEYS:
            v = pf.get(k)
            if v is not None:
                initial[k] = v
        _DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_DEDUP_PATH.parent, delete=False,
        ) as tmp:
            json.dump(initial, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _DEDUP_PATH)
        logger.info("dedup migration: populated state/dedup.json from portfolio.json")
    except Exception:
        logger.exception("dedup migration failed; using empty state")


def load_dedup() -> dict:
    """Return the full dedup state. Cheap (small file, fast JSON parse)."""
    with _DEDUP_LOCK:
        _migrate_from_portfolio_if_needed()
        if not _DEDUP_PATH.exists():
            return _empty_state()
        try:
            with _DEDUP_PATH.open() as f:
                state = json.load(f)
        except Exception:
            logger.exception("dedup load failed; returning empty state")
            return _empty_state()
        # Fill missing keys with defaults (forward-compat with new keys later).
        for k, v in _empty_state().items():
            state.setdefault(k, v)
        return state


def save_dedup(state: dict) -> None:
    """Atomic write of the full dedup state. Caller passes the mutated dict
    obtained from load_dedup()."""
    with _DEDUP_LOCK:
        _DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_DEDUP_PATH.parent, delete=False,
        ) as tmp:
            json.dump(state, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _DEDUP_PATH)


def update_dedup(updates: dict) -> None:
    """Apply partial updates (load + merge specific keys + save). Convenience
    for call-sites that only mutate one field."""
    with _DEDUP_LOCK:
        state = load_dedup()
        state.update(updates)
        save_dedup(state)
