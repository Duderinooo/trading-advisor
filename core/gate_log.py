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
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PATH = Path(__file__).resolve().parent.parent / "gate_blocks.jsonl"
_MAX_BYTES = 5 * 1024 * 1024  # 5MB → rotate to .1 backup, keep one


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


def summarize_gate_activity(day: str) -> dict:
    """Aggregate one day's gate decisions for the EOD digest.

    `day` is a "YYYY-MM-DD" prefix. Returns counts plus a `flags` list of
    human-readable anomalies worth a human look — the meta-learning layer
    that surfaces bot-logic bugs (repeated re-eval spam, Claude incoherence,
    chronic SL-sizing). Returns {} if no events that day.
    """
    events = [e for e in read_gate_blocks(limit=2000)
              if str(e.get("ts", "")).startswith(day)]
    if not events:
        return {}

    blocks = [e for e in events if e.get("blocked")]
    passes = [e for e in events if not e.get("blocked")]

    by_gate: dict[str, int] = {}
    pair_counts: dict[tuple, int] = {}   # (ticker, gate) -> block count
    for e in blocks:
        g = e.get("gate") or "?"
        by_gate[g] = by_gate.get(g, 0) + 1
        key = ((e.get("ticker") or "?"), g)
        pair_counts[key] = pair_counts.get(key, 0) + 1

    flags: list[str] = []

    # Re-eval spam: same ticker + same gate blocked ≥3× in one day. RS/edge are
    # intraday-stable — repeated blocks mean wasted Claude recs (cooldown target).
    for (tkr, gate), n in sorted(pair_counts.items(), key=lambda kv: -kv[1]):
        if n >= 3:
            flags.append(
                f"{tkr} × {gate} {n}× — Re-Eval-Spam (Cooldown sollte greifen)"
            )

    # Claude incoherence: recommend_exit on a ticker with no open position.
    no_pos = sorted({e.get("ticker") for e in blocks
                     if e.get("gate") == "exit_no_position"})
    for tkr in no_pos:
        flags.append(f"{tkr} exit_no_position — Claude empfahl Exit ohne Position")

    # Chronic SL-sizing: same ticker hits sl_distance ≥2× — Claude sets SL too
    # tight/wide for that name repeatedly (prompt-quality signal, not a one-off).
    sl_by_ticker: dict[str, int] = {}
    for e in blocks:
        if e.get("gate") == "sl_distance":
            t = e.get("ticker") or "?"
            sl_by_ticker[t] = sl_by_ticker.get(t, 0) + 1
    for tkr, n in sl_by_ticker.items():
        if n >= 2:
            flags.append(f"{tkr} × sl_distance {n}× — Claude SL-Sizing-Pattern")

    return {
        "day": day,
        "n_blocks": len(blocks),
        "n_passes": len(passes),
        "by_gate": dict(sorted(by_gate.items(), key=lambda kv: -kv[1])),
        "flags": flags,
    }
