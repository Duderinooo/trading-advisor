"""Append-only JSONL log of entry-gate decisions.

Every blocked entry recommendation gets a single line; passes get logged too so
the dashboard can compute pass/block ratio per gate. File is stable JSONL — one
event per line, never rewritten — so concurrent appenders are safe without a lock.

Schema:
  {"ts": "2026-04-26 10:32", "ticker": "NVD.DE", "gate": "edge",
   "blocked": true, "reason": "edge 0.02 < 0.04",
   "context": {...gate-specific facts...}}
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PATH = Path(__file__).resolve().parent.parent / "gate_blocks.jsonl"
_MAX_BYTES = 5 * 1024 * 1024  # 5MB → rotate to .1 backup, keep one

# Test isolation. main.py sets TA_GATE_LOG_ENABLED=1 at startup so the
# production bot writes gate decisions; tests run without that flag and
# thus skip the writes. Prevents incidents like 2026-05-23 where
# production gate_blocks.jsonl had 92 BAS.DE blocks in one day — all
# from test_decision_result.py runs during the dev session.
_ENABLED_ENV_KEY = "TA_GATE_LOG_ENABLED"


def _rotate_if_needed():
    try:
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _MAX_BYTES:
            backup = _LOG_PATH.with_suffix(".jsonl.1")
            if backup.exists():
                backup.unlink()
            _LOG_PATH.rename(backup)
    except OSError as e:
        logger.warning("gate_log rotate failed: %s", e)


def log_gate(ticker: str, gate: str, blocked: bool, reason: str = "", context: dict | None = None):
    """Append one gate-decision line to gate_blocks.jsonl. Never raises."""
    if not os.environ.get(_ENABLED_ENV_KEY):
        return
    try:
        _rotate_if_needed()
        rec = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "ticker": (ticker or "?").upper(),
            "gate": gate,
            "blocked": bool(blocked),
            "reason": reason or "",
            "context": context or {},
        }
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("gate_log write failed: %s", e)


def read_gate_blocks(limit: int = 500) -> list[dict]:
    """Return last `limit` events (newest last). Used by web /api route + backtest."""
    if not _LOG_PATH.exists():
        return []
    try:
        with open(_LOG_PATH) as f:
            lines = f.readlines()[-limit:]
        out = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except OSError as e:
        logger.warning("gate_log read failed: %s", e)
        return []
