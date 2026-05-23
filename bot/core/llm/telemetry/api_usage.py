"""Claude API call tracking + cooldown gating.

Both main loop and Telegram listener thread call increment_usage / can_make_api_call.
Without locking + atomic writes, racy reads of `last_analysis` could let two calls
sneak through the cooldown gate simultaneously, OR a crash mid-write could corrupt
the JSON and reset counters silently.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, date
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# File lives in project root (parent of core/)
USAGE_FILE = Path(__file__).resolve().parent.parent / ".api_usage.json"

# Guards every load-modify-save sequence on .api_usage.json.
_usage_lock = threading.RLock()


def _read_usage_file() -> dict:
    """Read raw file. Returns fresh dict on missing/corrupt."""
    if not USAGE_FILE.exists():
        return {"date": str(date.today()), "calls": 0, "last_analysis": None}
    try:
        with open(USAGE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Corrupt .api_usage.json (%s) — resetting counters", e)
        return {"date": str(date.today()), "calls": 0, "last_analysis": None}


def _write_usage_file(data: dict) -> None:
    """Atomic write via tmp + rename. Crash-safe."""
    fd, tmp_path = tempfile.mkstemp(
        prefix=".api_usage_", suffix=".json", dir=USAGE_FILE.parent,
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, USAGE_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def get_usage_data() -> dict:
    """Get usage tracking data. Resets counter if the stored date != today."""
    with _usage_lock:
        data = _read_usage_file()
        if data.get("date") != str(date.today()):
            return {"date": str(date.today()), "calls": 0, "last_analysis": None}
        return data


def get_daily_usage() -> int:
    return get_usage_data().get("calls", 0)


def get_minutes_since_last_analysis() -> float:
    data = get_usage_data()
    last = data.get("last_analysis")
    if not last:
        return float("inf")
    try:
        last_time = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return float("inf")
    return (datetime.now() - last_time).total_seconds() / 60


def increment_usage(forced: bool = False):
    """Increment daily counter. `forced=True` also updates `last_forced` timestamp
    so geo-news floods can't burn the daily cap in minutes."""
    with _usage_lock:
        data = _read_usage_file()
        # Date roll-over inside the lock so concurrent increments can't both
        # land on the same stale day.
        if data.get("date") != str(date.today()):
            data = {"date": str(date.today()), "calls": 0, "last_analysis": None}
        data["calls"] = int(data.get("calls", 0)) + 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data["last_analysis"] = now
        if forced:
            data["last_forced"] = now
        _write_usage_file(data)


def can_make_api_call(force: bool = False, bypass_cooldown: bool = False) -> tuple[bool, str]:
    """Check if we should make an API call. Returns (allowed, reason).

    - Hard daily cap (safety).
    - Regular cooldown between calls.
    - `force` bypasses the regular cooldown but has its own softer cooldown
      so a news-flood can't burn the daily cap in minutes.
    - `bypass_cooldown` skips even the forced-cooldown (manual /morning etc.).
      Daily cap still enforced.

    Held under the same lock as increment_usage so the read-decide-write race
    that previously let two threads simultaneously pass the cap can't happen.
    """
    with _usage_lock:
        data = _read_usage_file()
        if data.get("date") != str(date.today()):
            data = {"date": str(date.today()), "calls": 0, "last_analysis": None}
        calls_today = int(data.get("calls", 0))

        if calls_today >= config.MAX_ANALYSES_PER_DAY:
            return False, f"Daily cap reached ({config.MAX_ANALYSES_PER_DAY})"

        if bypass_cooldown:
            return True, "OK (cooldown bypassed)"

        if force:
            last_forced = data.get("last_forced")
            if last_forced:
                try:
                    last_forced_time = datetime.strptime(last_forced, "%Y-%m-%d %H:%M:%S")
                    mins = (datetime.now() - last_forced_time).total_seconds() / 60
                    if mins < config.MIN_MINUTES_BETWEEN_FORCED_ANALYSES:
                        remaining = config.MIN_MINUTES_BETWEEN_FORCED_ANALYSES - mins
                        return False, f"Forced-call cooldown ({remaining:.0f}min)"
                except ValueError:
                    pass  # corrupt timestamp, fall through to allow
            return True, "OK (forced)"

        last = data.get("last_analysis")
        if last:
            try:
                last_time = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                minutes_since = (datetime.now() - last_time).total_seconds() / 60
                if minutes_since < config.MIN_MINUTES_BETWEEN_ANALYSES:
                    remaining = config.MIN_MINUTES_BETWEEN_ANALYSES - minutes_since
                    return False, f"Cooldown ({remaining:.0f}min remaining)"
            except ValueError:
                pass

        return True, "OK"
