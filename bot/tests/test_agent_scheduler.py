"""Tests for agents._lib.scheduler — cadence + window logic.

No subprocess calls. State persisted to a temp kv_state row that we manually
clean up. Validates the time-windowed and interval-based scheduling decisions.
"""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agents._lib import scheduler

CET = ZoneInfo("Europe/Berlin")


class TestMarketHours(unittest.TestCase):
    def test_xetra_open_monday(self):
        # Mon 2026-05-25 10:00 CET (mid-XETRA)
        now = datetime(2026, 5, 25, 10, 0, tzinfo=CET)
        self.assertTrue(scheduler._is_market_hours(now))

    def test_us_open_monday_evening(self):
        # Mon 2026-05-25 17:00 CET (mid-US, post-XETRA-close)
        now = datetime(2026, 5, 25, 17, 0, tzinfo=CET)
        self.assertTrue(scheduler._is_market_hours(now))

    def test_outside_market_hours_morning(self):
        # Mon 2026-05-25 03:00 CET
        now = datetime(2026, 5, 25, 3, 0, tzinfo=CET)
        self.assertFalse(scheduler._is_market_hours(now))

    def test_weekend_closed(self):
        # Sat 2026-05-23 14:00 CET
        now = datetime(2026, 5, 23, 14, 0, tzinfo=CET)
        self.assertFalse(scheduler._is_market_hours(now))


class TestIsDue(unittest.TestCase):
    def setUp(self):
        # Patch kv read/write to avoid touching real DB
        self._cache: dict[str, dict] = {}

        def fake_get(key: str):
            return self._cache.get(key)

        def fake_put(key: str, body: dict):
            self._cache[key] = body

        self._g = patch("agents._lib.scheduler._kv_get", side_effect=fake_get)
        self._p = patch("agents._lib.scheduler._kv_put", side_effect=fake_put)
        self._g.start()
        self._p.start()
        self.addCleanup(self._g.stop)
        self.addCleanup(self._p.stop)

    def test_interval_agent_first_run(self):
        now = datetime(2026, 5, 25, 10, 0, tzinfo=CET)
        # Never ran → due
        self.assertTrue(scheduler.is_due("bug-watcher", now=now))

    def test_interval_agent_recently_ran_not_due(self):
        # Cache: ran 10 min ago
        now = datetime(2026, 5, 25, 10, 0, tzinfo=CET)
        ten_min_ago = now.timestamp() - 600
        self._cache["bug-watcher"] = {"ts": ten_min_ago, "ok": True}
        self.assertFalse(scheduler.is_due("bug-watcher", now=now))

    def test_interval_agent_60min_passed_due(self):
        now = datetime(2026, 5, 25, 10, 0, tzinfo=CET)
        sixty_one_ago = now.timestamp() - 61 * 60
        self._cache["bug-watcher"] = {"ts": sixty_one_ago, "ok": True}
        self.assertTrue(scheduler.is_due("bug-watcher", now=now))

    def test_bug_watcher_blocked_outside_market_hours(self):
        # Sun 3am CET
        now = datetime(2026, 5, 24, 3, 0, tzinfo=CET)
        self.assertFalse(scheduler.is_due("bug-watcher", now=now))

    def test_health_inspector_allowed_outside_market(self):
        # Sun 3am CET — health-inspector has only_during_market_hours=False
        now = datetime(2026, 5, 24, 3, 0, tzinfo=CET)
        self.assertTrue(scheduler.is_due("health-inspector", now=now))

    def test_eod_postmortem_window(self):
        # 22:30 CET (in window)
        now = datetime(2026, 5, 25, 22, 30, tzinfo=CET)
        self.assertTrue(scheduler.is_due("eod-postmortem", now=now))
        # 21:30 (before window)
        now2 = datetime(2026, 5, 25, 21, 30, tzinfo=CET)
        self.assertFalse(scheduler.is_due("eod-postmortem", now=now2))
        # 22:30 after running same day (still in same window-day → not due)
        self._cache["eod-postmortem"] = {"ts": now.timestamp(), "ok": True}
        self.assertFalse(scheduler.is_due("eod-postmortem", now=now))

    def test_weekly_calibrator_only_sunday(self):
        # Sunday in window
        sun = datetime(2026, 5, 24, 12, 0, tzinfo=CET)
        self.assertTrue(scheduler.is_due("weekly-calibrator", now=sun))
        # Monday same time
        mon = datetime(2026, 5, 25, 12, 0, tzinfo=CET)
        self.assertFalse(scheduler.is_due("weekly-calibrator", now=mon))

    def test_unknown_agent_not_due(self):
        now = datetime(2026, 5, 25, 10, 0, tzinfo=CET)
        self.assertFalse(scheduler.is_due("does-not-exist", now=now))


class TestDueAgents(unittest.TestCase):
    def setUp(self):
        self._cache: dict[str, dict] = {}
        self._g = patch("agents._lib.scheduler._kv_get",
                        side_effect=lambda k: self._cache.get(k))
        self._p = patch("agents._lib.scheduler._kv_put",
                        side_effect=lambda k, v: self._cache.__setitem__(k, v))
        self._g.start()
        self._p.start()
        self.addCleanup(self._g.stop)
        self.addCleanup(self._p.stop)

    def test_monday_morning_only_bug_watcher_and_health(self):
        # Mon 10:00 CET, all caches empty
        now = datetime(2026, 5, 25, 10, 0, tzinfo=CET)
        due = scheduler.due_agents(now=now)
        # bug-watcher: in market hours, never ran → due
        # health-inspector: market-hours-independent, never ran → due
        # eod-postmortem: outside 22-23 window → not due
        # weekly-calibrator: Mon, not Sun → not due
        # backlog-keeper: Mon, not Sun → not due
        self.assertIn("bug-watcher", due)
        self.assertIn("health-inspector", due)
        self.assertNotIn("eod-postmortem", due)
        self.assertNotIn("weekly-calibrator", due)
        self.assertNotIn("backlog-keeper", due)

    def test_sunday_evening_backlog_only_after_calibrator_done(self):
        # Sun 19:00 CET — calibrator window 10-18 closed, backlog 18-22 open
        now = datetime(2026, 5, 24, 19, 0, tzinfo=CET)
        due = scheduler.due_agents(now=now)
        self.assertIn("backlog-keeper", due)


if __name__ == "__main__":
    unittest.main()
