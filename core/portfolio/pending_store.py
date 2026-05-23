"""Pending recommendations out of portfolio.json → state/pending.json.

pending_recommendations = recs Sonnet/Haiku emitted but user hasn't /confirmed
yet (entry / add / update / exit kinds). Stored in own file because they're
short-lived (≤PENDING_REC_TTL_HOURS) and turn over frequently — every confirm
mutates the list, plus EOD cleanup, plus exit-reminder ticks. Mixing them with
positions / cash made portfolio.json write-noisy.

Storage: state/pending.json — list[dict], own lock.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path


logger = logging.getLogger(__name__)


_PENDING_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / "pending.json"
)
_PENDING_LOCK = threading.RLock()


def _migrate_from_portfolio_if_needed() -> None:
    """One-shot: populate state/pending.json from portfolio.json if missing."""
    if _PENDING_PATH.exists():
        return
    try:
        from core.portfolio.io import _load_portfolio_raw
        pf = _load_portfolio_raw()
        initial = pf.get("pending_recommendations") or []
        _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_PENDING_PATH.parent, delete=False,
        ) as tmp:
            json.dump(initial, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _PENDING_PATH)
        logger.info("pending migration: populated state/pending.json with %d recs",
                    len(initial))
    except Exception:
        logger.exception("pending migration failed; using empty list")


def load_pending() -> list[dict]:
    """Return current pending-rec list."""
    with _PENDING_LOCK:
        _migrate_from_portfolio_if_needed()
        if not _PENDING_PATH.exists():
            return []
        try:
            with _PENDING_PATH.open() as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            logger.exception("pending load failed; returning empty")
            return []


def save_pending(pending: list[dict]) -> None:
    """Atomic write of the full pending-rec list."""
    with _PENDING_LOCK:
        _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_PENDING_PATH.parent, delete=False,
        ) as tmp:
            json.dump(pending, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _PENDING_PATH)


def append_pending(rec: dict) -> None:
    """Add one rec to the list. Atomic load+append+save."""
    with _PENDING_LOCK:
        pending = load_pending()
        pending.append(rec)
        save_pending(pending)
