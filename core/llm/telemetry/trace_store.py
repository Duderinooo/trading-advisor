"""Trace state — per-mode pipeline trace persisted out of portfolio.json.

Previously portfolio.json carried last_morning_trace + last_event_trace +
last_opening_trace_xetra/us. Telemetry not trading state — own file.

Storage: state/traces.json
Schema: {
  "last_morning_trace": {...},
  "last_event_trace": {...},
  "last_opening_trace_xetra": {...},
  "last_opening_trace_us": {...},
}
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path


logger = logging.getLogger(__name__)


_TRACES_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "state" / "traces.json"
)
_TRACES_LOCK = threading.RLock()
_TRACE_KEYS = (
    "last_morning_trace", "last_event_trace",
    "last_opening_trace_xetra", "last_opening_trace_us",
    "last_opening_trace",  # legacy bucket if event_context not parsed
)


def _migrate_from_portfolio_if_needed() -> None:
    """One-shot: populate state/traces.json from portfolio.json if missing."""
    if _TRACES_PATH.exists():
        return
    try:
        from core.portfolio.io import _load_portfolio_raw
        pf = _load_portfolio_raw()
        initial = {k: pf[k] for k in _TRACE_KEYS if k in pf}
        _TRACES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_TRACES_PATH.parent, delete=False,
        ) as tmp:
            json.dump(initial, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _TRACES_PATH)
        logger.info("traces migration: populated state/traces.json from portfolio.json (%d keys)",
                    len(initial))
    except Exception:
        logger.exception("traces migration failed; using empty state")


def load_traces() -> dict:
    with _TRACES_LOCK:
        _migrate_from_portfolio_if_needed()
        if not _TRACES_PATH.exists():
            return {}
        try:
            with _TRACES_PATH.open() as f:
                return json.load(f)
        except Exception:
            logger.exception("traces load failed; returning empty")
            return {}


def save_trace(key: str, trace: dict) -> None:
    """Write a single trace under `key` (e.g. last_morning_trace).
    Atomic; other keys preserved."""
    with _TRACES_LOCK:
        existing = load_traces()
        existing[key] = trace
        _TRACES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_TRACES_PATH.parent, delete=False,
        ) as tmp:
            json.dump(existing, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _TRACES_PATH)
