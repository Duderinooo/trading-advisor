"""Runtime / ephemeral state out of portfolio.json → state/runtime.json.

Previously portfolio.json carried:
- heartbeat        — live quotes + last_tick (mutated every 60s by main loop)
- entry_gate_cooldowns — RS/edge/red-team intraday-stable gate cooldowns
- transient_retries    — per-day API error retry counters
- correlation_matrix   — pairwise corr over open positions (refreshed each morning)

All four are derived/ephemeral state — not trading invariants. Pulling them
out lowers portfolio.json write frequency 60×/min → 0 (heartbeat alone) and
shrinks the file by 50%+ at typical sizes.

Storage: state/runtime.json — own lock, atomic writes.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path


logger = logging.getLogger(__name__)


_RUNTIME_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / "runtime.json"
)
_RUNTIME_LOCK = threading.RLock()
_RUNTIME_KEYS = (
    "heartbeat", "entry_gate_cooldowns", "transient_retries", "correlation_matrix",
)


def _migrate_from_portfolio_if_needed() -> None:
    """One-shot: populate state/runtime.json from portfolio.json if missing."""
    if _RUNTIME_PATH.exists():
        return
    try:
        from core.portfolio.io import load_portfolio
        pf = load_portfolio()
        initial = {k: pf[k] for k in _RUNTIME_KEYS if k in pf}
        _RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_RUNTIME_PATH.parent, delete=False,
        ) as tmp:
            json.dump(initial, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _RUNTIME_PATH)
        logger.info("runtime migration: populated state/runtime.json from portfolio.json (%d keys)",
                    len(initial))
    except Exception:
        logger.exception("runtime migration failed; using empty state")


def load_runtime() -> dict:
    """Return full runtime state. Missing keys default to empty dict."""
    with _RUNTIME_LOCK:
        _migrate_from_portfolio_if_needed()
        if not _RUNTIME_PATH.exists():
            return {}
        try:
            with _RUNTIME_PATH.open() as f:
                return json.load(f)
        except Exception:
            logger.exception("runtime load failed; returning empty")
            return {}


def save_runtime(state: dict) -> None:
    """Atomic write of the full runtime dict."""
    with _RUNTIME_LOCK:
        _RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_RUNTIME_PATH.parent, delete=False,
        ) as tmp:
            json.dump(state, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _RUNTIME_PATH)


def update_runtime(updates: dict) -> None:
    """Partial-update convenience: load + merge top-level keys + save."""
    with _RUNTIME_LOCK:
        state = load_runtime()
        state.update(updates)
        save_runtime(state)
