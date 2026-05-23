"""Closed trades out of portfolio.json → state/closed_trades.jsonl.

closed_trades is an append-mostly list that grows unbounded. Keeping it in
portfolio.json bloated the file + made every load+save touch the full history.

Storage: state/closed_trades.jsonl — one trade per line (newest at bottom).
Atomic full-file rewrite via tmp+rename — closed_trades doesn't change often
(only /close handler + SL/TP loop + partial-fill events), so write-cost is
acceptable. Future SQLite migration trivial because the line-per-record
format already mirrors a table row.

Used via transparent shim in core/portfolio/io.py: load_portfolio() splices
closed_trades in from this file; save_portfolio() extracts + persists here.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path


logger = logging.getLogger(__name__)


_CLOSED_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / "closed_trades.jsonl"
)
_CLOSED_LOCK = threading.RLock()


def _migrate_from_portfolio_if_needed() -> None:
    """One-shot: populate state/closed_trades.jsonl from portfolio.json if missing."""
    if _CLOSED_PATH.exists():
        return
    try:
        from core.portfolio.io import _load_portfolio_raw
        pf = _load_portfolio_raw()
        initial = pf.get("closed_trades") or []
        _CLOSED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_CLOSED_PATH.parent, delete=False,
        ) as tmp:
            for trade in initial:
                tmp.write(json.dumps(trade) + "\n")
            tmp_path = tmp.name
        os.replace(tmp_path, _CLOSED_PATH)
        logger.info("closed_trades migration: persisted %d trades to state/closed_trades.jsonl",
                    len(initial))
    except Exception:
        logger.exception("closed_trades migration failed; using empty list")


def load_closed_trades() -> list[dict]:
    """Return full closed_trades list. Reads line-by-line; tolerates partial
    JSON parse failures (skips bad lines)."""
    with _CLOSED_LOCK:
        _migrate_from_portfolio_if_needed()
        if not _CLOSED_PATH.exists():
            return []
        out: list[dict] = []
        try:
            with _CLOSED_PATH.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed closed_trades line: %s",
                                       line[:80])
        except Exception:
            logger.exception("closed_trades load failed; returning empty")
            return []
        return out


def save_closed_trades(trades: list[dict]) -> None:
    """Atomic full rewrite. Used by the transparent shim in save_portfolio."""
    with _CLOSED_LOCK:
        _CLOSED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=_CLOSED_PATH.parent, delete=False,
        ) as tmp:
            for trade in trades:
                tmp.write(json.dumps(trade) + "\n")
            tmp_path = tmp.name
        os.replace(tmp_path, _CLOSED_PATH)


def append_closed_trade(trade: dict) -> None:
    """Append one trade — true append (no rewrite)."""
    with _CLOSED_LOCK:
        _CLOSED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CLOSED_PATH.open("a") as f:
            f.write(json.dumps(trade) + "\n")
