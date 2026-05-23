"""Tail-trim JSONL telemetry files to keep them from growing unbounded.

Targeted at write-only audit logs: claude_calls.jsonl (every Claude
call), gate_blocks.jsonl (every gate block), analytics/*.jsonl (per-day
snapshots). Each is line-per-record append, so a tail-N rewrite under
tmp+rename preserves the most recent activity without touching readers
mid-rotation.

Limits chosen to balance disk vs forensic value:
- claude_calls: 50 entries — each ~8KB (full prompt + response). A week
  of normal activity. /api/claude-calls dashboard widget shows last 200
  but tolerates fewer.
- gate_blocks: 500 entries — small (~50B each), keep more for tuning
  evidence (FNR analysis needs sample-size).
- analytics/decisions.jsonl: 200 — one per entry-rec, modest size.
- analytics/gate_outcomes_pending.jsonl: 500 — blocked-entry follow-ups,
  short-lived (resolved within ~5 trading days).

Wired into services.summary._run_db_backup → runs alongside the EOD
snapshot, fail-soft.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


BOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class JsonlTarget:
    relpath: str
    keep_lines: int


_TARGETS: tuple[JsonlTarget, ...] = (
    JsonlTarget("claude_calls.jsonl", 50),
    JsonlTarget("gate_blocks.jsonl", 500),
    JsonlTarget("analytics/decisions.jsonl", 200),
    JsonlTarget("analytics/gate_outcomes_pending.jsonl", 500),
)


def _trim_one(target: JsonlTarget) -> tuple[int, int]:
    """Trim one file to its keep_lines. Returns (before, after) line count.
    If file is missing or already within limit, returns (n, n)."""
    path = BOT_DIR / target.relpath
    if not path.exists():
        return (0, 0)
    with path.open() as f:
        lines = f.readlines()
    before = len(lines)
    if before <= target.keep_lines:
        return (before, before)
    keep = lines[-target.keep_lines:]
    # tmp+rename in the same directory so os.replace is atomic on the
    # filesystem level. Don't use NamedTemporaryFile.delete=True — we want
    # the file to survive across the rename.
    fd, tmp_path = tempfile.mkstemp(
        prefix="." + path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w") as out:
            out.writelines(keep)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return (before, target.keep_lines)


def rotate_all() -> dict[str, tuple[int, int]]:
    """Trim every configured target. Returns per-file (before, after).
    Errors per file are logged but don't abort the whole rotation."""
    out: dict[str, tuple[int, int]] = {}
    for t in _TARGETS:
        try:
            out[t.relpath] = _trim_one(t)
        except Exception:
            logger.exception("Rotation failed for %s", t.relpath)
            out[t.relpath] = (-1, -1)
    return out


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )
    results = rotate_all()
    for relpath, (before, after) in results.items():
        if before == 0:
            logger.info("rotate_jsonl: %s (missing, skipped)", relpath)
        elif before == after:
            logger.info("rotate_jsonl: %s (%d lines, within limit)",
                        relpath, before)
        elif before == -1:
            logger.warning("rotate_jsonl: %s FAILED (see exception)", relpath)
        else:
            logger.info("rotate_jsonl: %s trimmed %d -> %d", relpath, before, after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
