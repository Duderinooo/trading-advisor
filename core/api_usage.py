"""Claude API call tracking + cooldown gating."""

import json
import logging
from datetime import datetime, date
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# File lives in project root (parent of core/)
USAGE_FILE = Path(__file__).resolve().parent.parent / ".api_usage.json"


def get_usage_data() -> dict:
    """Get usage tracking data. Resets counter if the stored date != today."""
    if not USAGE_FILE.exists():
        return {"date": str(date.today()), "calls": 0, "last_analysis": None}

    with open(USAGE_FILE) as f:
        data = json.load(f)

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
    last_time = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    return (datetime.now() - last_time).total_seconds() / 60


def increment_usage(forced: bool = False):
    """Increment daily counter. `forced=True` also updates `last_forced` timestamp
    so geo-news floods can't burn the daily cap in minutes."""
    data = get_usage_data()
    data["calls"] = data.get("calls", 0) + 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["last_analysis"] = now
    if forced:
        data["last_forced"] = now
    data["date"] = str(date.today())

    with open(USAGE_FILE, "w") as f:
        json.dump(data, f)


def can_make_api_call(force: bool = False) -> tuple[bool, str]:
    """Check if we should make an API call. Returns (allowed, reason).

    - Hard daily cap (safety).
    - Regular cooldown between calls.
    - `force` bypasses the regular cooldown but has its own softer cooldown
      so a news-flood can't burn the daily cap in minutes.
    """
    calls_today = get_daily_usage()

    if calls_today >= config.MAX_ANALYSES_PER_DAY:
        return False, f"Daily cap reached ({config.MAX_ANALYSES_PER_DAY})"

    if force:
        data = get_usage_data()
        last_forced = data.get("last_forced")
        if last_forced:
            last_forced_time = datetime.strptime(last_forced, "%Y-%m-%d %H:%M:%S")
            mins = (datetime.now() - last_forced_time).total_seconds() / 60
            if mins < config.MIN_MINUTES_BETWEEN_FORCED_ANALYSES:
                remaining = config.MIN_MINUTES_BETWEEN_FORCED_ANALYSES - mins
                return False, f"Forced-call cooldown ({remaining:.0f}min)"
        return True, "OK (forced)"

    minutes_since = get_minutes_since_last_analysis()
    if minutes_since < config.MIN_MINUTES_BETWEEN_ANALYSES:
        remaining = config.MIN_MINUTES_BETWEEN_ANALYSES - minutes_since
        return False, f"Cooldown ({remaining:.0f}min remaining)"

    return True, "OK"
