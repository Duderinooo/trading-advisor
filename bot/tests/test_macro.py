"""Unit tests for macro.py pure-logic helpers.

Critical-event keyword filter, UTC→Berlin conversion, imminence check.
The Finnhub fetch (fetch_economic_events) is out of scope.
"""

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import macro


class TestIsCritical(unittest.TestCase):
    def test_fomc_is_critical(self):
        self.assertTrue(macro._is_critical("FOMC Rate Decision"))

    def test_cpi_is_critical(self):
        self.assertTrue(macro._is_critical("US Core CPI m/m"))

    def test_sentiment_indicator_not_critical(self):
        # ZEW / Ifo / Consumer Confidence deliberately excluded
        self.assertFalse(macro._is_critical("ZEW Economic Sentiment"))

    def test_empty_and_none_not_critical(self):
        self.assertFalse(macro._is_critical(""))
        self.assertFalse(macro._is_critical(None))


class TestToBerlin(unittest.TestCase):
    def test_winter_utc_offset_plus_one(self):
        # 15 Jan = CET (UTC+1): 12:00 UTC → 13:00 Berlin
        berlin = macro._to_berlin({"date": "2026-01-15", "time": "12:00"})
        self.assertEqual(berlin.strftime("%H:%M"), "13:00")

    def test_summer_utc_offset_plus_two(self):
        # 15 Jul = CEST (UTC+2): 12:00 UTC → 14:00 Berlin
        berlin = macro._to_berlin({"date": "2026-07-15", "time": "12:00"})
        self.assertEqual(berlin.strftime("%H:%M"), "14:00")

    def test_missing_time_returns_none(self):
        self.assertIsNone(macro._to_berlin({"date": "2026-01-15", "time": ""}))

    def test_bad_time_returns_none(self):
        self.assertIsNone(macro._to_berlin({"date": "2026-01-15", "time": "garbage"}))


class TestHasImminentEvent(unittest.TestCase):
    @staticmethod
    def _event_in(hours_from_now):
        target = datetime.now(ZoneInfo("UTC")) + timedelta(hours=hours_from_now)
        return {"date": target.strftime("%Y-%m-%d"), "time": target.strftime("%H:%M")}

    def test_event_within_window_is_imminent(self):
        self.assertTrue(macro.has_imminent_event([self._event_in(1)], within_hours=3))

    def test_event_beyond_window_not_imminent(self):
        self.assertFalse(macro.has_imminent_event([self._event_in(10)], within_hours=3))

    def test_past_event_not_imminent(self):
        self.assertFalse(macro.has_imminent_event([self._event_in(-2)], within_hours=3))

    def test_empty_list_not_imminent(self):
        self.assertFalse(macro.has_imminent_event([], within_hours=3))


if __name__ == "__main__":
    unittest.main()
