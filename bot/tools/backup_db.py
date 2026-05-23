"""Snapshot the bot's SQLite store to a timestamped backup file.

Uses the SQLite Online Backup API (Connection.backup) so it runs safely
while the bot is writing — no need to stop the bot or take any lock at
the application layer. Output goes to state/backups/, with a 14-day
retention (older snapshots are purged on each run).

Designed to be called from cron (or `launchd`):

    0 3 * * *  cd /path/to/bot && ../venv/bin/python -m tools.backup_db

Daily run, ~88KB per snapshot, ~1.2MB over 14 days. Cheap.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


logger = logging.getLogger(__name__)


BOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BOT_DIR / "state" / "bot.db"
BACKUP_DIR = BOT_DIR / "state" / "backups"
RETENTION_DAYS = 14


def snapshot() -> Path:
    """Create one timestamped snapshot, return the path written."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB not found: {DB_PATH}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = BACKUP_DIR / f"bot.db.{ts}"

    src = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        dst = sqlite3.connect(out)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return out


def prune(retention_days: int = RETENTION_DAYS) -> int:
    """Delete snapshots older than retention_days. Returns count removed."""
    if not BACKUP_DIR.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for p in BACKUP_DIR.glob("bot.db.*"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            logger.exception("Failed to prune %s", p)
    return removed


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )
    try:
        out = snapshot()
        size_kb = out.stat().st_size / 1024
        removed = prune()
        logger.info(
            "backup_db: wrote %s (%.1f KB), pruned %d old snapshots",
            out.name, size_kb, removed,
        )
        return 0
    except Exception:
        logger.exception("backup_db failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
