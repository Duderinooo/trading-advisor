"""Scheduler: tracks when each monitoring agent last ran + whether it's due.

State lives in kv_state (namespace='agent_schedule'). Simple last-run-timestamp
per agent. Cadence checks are pure functions — caller decides whether to fire.

No async, no threads. main-loop calls `due_agents()` once per tick and runs
whatever is overdue. Single-agent fires take 10-60s which the main loop can
absorb between price-checks.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import json as _json

from core.db import connect

logger = logging.getLogger(__name__)

_NS = "agent_schedule"
_CET = ZoneInfo("Europe/Berlin")


def _kv_get(key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT body FROM kv_state WHERE namespace=? AND key=?",
            (_NS, key),
        ).fetchone()
    if not row:
        return None
    try:
        return _json.loads(row[0])
    except Exception:
        return None


def _kv_put(key: str, body: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (namespace, key, body) VALUES (?,?,?)",
            (_NS, key, _json.dumps(body)),
        )
        conn.commit()


@dataclass
class AgentCadence:
    """When + how often an agent should fire."""
    name: str
    interval_minutes: int | None = None  # fixed cadence (None = uses time-window)
    time_window: tuple[int, int] | None = None  # (hh_start, hh_end) CET
    days_of_week: set[int] | None = None  # 0=Mon..6=Sun (None = every day)
    only_during_market_hours: bool = True


SCHEDULES: dict[str, AgentCadence] = {
    "bug-watcher": AgentCadence(
        name="bug-watcher",
        interval_minutes=60,
        only_during_market_hours=True,
    ),
    "health-inspector": AgentCadence(
        name="health-inspector",
        interval_minutes=240,  # every 4h
        only_during_market_hours=False,  # also catches overnight issues
    ),
    "eod-postmortem": AgentCadence(
        name="eod-postmortem",
        time_window=(22, 23),  # CET, after US-close
        only_during_market_hours=False,
    ),
    "weekly-calibrator": AgentCadence(
        name="weekly-calibrator",
        days_of_week={6},  # Sunday
        time_window=(10, 18),
        only_during_market_hours=False,
    ),
    "backlog-keeper": AgentCadence(
        name="backlog-keeper",
        days_of_week={6},  # Sunday
        time_window=(18, 22),
        only_during_market_hours=False,
    ),
}


def last_run_ts(name: str) -> float | None:
    """Read last successful run timestamp. None if never run."""
    rec = _kv_get(name)
    if not rec:
        return None
    ts = rec.get("ts")
    if isinstance(ts, (int, float)):
        return float(ts)
    return None


def mark_ran(name: str, *, ok: bool = True, meta: dict | None = None) -> None:
    """Persist run timestamp + meta."""
    payload = {"ts": time.time(), "ok": ok}
    if meta:
        payload["meta"] = meta
    _kv_put(name, payload)


def _now_cet() -> datetime:
    return datetime.now(_CET)


def _is_market_hours(now: datetime) -> bool:
    """XETRA 09:00–17:30 OR US 15:30–22:00 CET. Weekend = False."""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    xetra = 9 * 60 <= hm <= 17 * 60 + 30
    us = 15 * 60 + 30 <= hm <= 22 * 60
    return xetra or us


def is_due(name: str, *, now: datetime | None = None) -> bool:
    """Should this agent fire right now?"""
    sched = SCHEDULES.get(name)
    if sched is None:
        return False
    now = now or _now_cet()

    if sched.only_during_market_hours and not _is_market_hours(now):
        return False

    if sched.days_of_week is not None and now.weekday() not in sched.days_of_week:
        return False

    if sched.time_window is not None:
        h_start, h_end = sched.time_window
        if not (h_start <= now.hour < h_end):
            return False

    last = last_run_ts(name)
    if sched.interval_minutes is not None:
        if last is None:
            return True
        elapsed_min = (now.timestamp() - last) / 60
        return elapsed_min >= sched.interval_minutes

    # Time-window-only schedule: fire once per matching window.
    if last is None:
        return True
    last_dt = datetime.fromtimestamp(last, _CET)
    # Same calendar day + within same window = already ran today
    if last_dt.date() == now.date():
        if sched.time_window is None:
            return False
        h_start, _ = sched.time_window
        if last_dt.hour >= h_start:
            return False
    return True


def due_agents(now: datetime | None = None) -> list[str]:
    """Names of agents that should fire right now (in scheduled order)."""
    now = now or _now_cet()
    return [name for name in SCHEDULES if is_due(name, now=now)]
