"""Unit tests for core.market_data pure-logic helpers.

Network fetching (_fetch_ticker, get_market_data) is out of scope — only the
deterministic Xetra-session-fraction projection helper is covered.
"""

import unittest
from datetime import datetime

import core.data.market_data as M


class TestSessionFraction(unittest.TestCase):
    # 2026-05-19 is a Tuesday, 2026-05-23 a Saturday (asserted below).
    @staticmethod
    def _at(h, mi, day=19):
        return datetime(2026, 5, day, h, mi)

    def test_pre_open_is_full_day(self):
        # 08:00 — yfinance volume is then the last completed session
        self.assertEqual(M._session_fraction(self._at(8, 0)), 1.0)

    def test_at_open_is_full_day(self):
        self.assertEqual(M._session_fraction(self._at(9, 0)), 1.0)

    def test_at_close_is_full_day(self):
        self.assertEqual(M._session_fraction(self._at(17, 30)), 1.0)

    def test_post_close_is_full_day(self):
        self.assertEqual(M._session_fraction(self._at(18, 0)), 1.0)

    def test_session_midpoint(self):
        # 13:15 = 255min into the 510min session → 0.5
        self.assertAlmostEqual(M._session_fraction(self._at(13, 15)), 0.5, places=6)

    def test_early_session_floored(self):
        # 09:30 → raw 30/510 ≈ 0.059, below the floor → clamped
        self.assertEqual(
            M._session_fraction(self._at(9, 30)), M._MIN_SESSION_FRACTION)

    def test_weekend_is_full_day(self):
        sat = self._at(13, 15, day=23)
        self.assertEqual(sat.weekday(), 5)  # guard: 2026-05-23 is a Saturday
        self.assertEqual(M._session_fraction(sat), 1.0)

    def test_fraction_monotonic_within_session(self):
        prev = -1.0
        for h in range(10, 18):
            f = M._session_fraction(self._at(h, 0))
            self.assertGreaterEqual(f, prev)
            self.assertLessEqual(f, 1.0)
            prev = f


class TestCountConsecutiveHigherLows(unittest.TestCase):
    def test_all_higher(self):
        self.assertEqual(M._count_consecutive_higher_lows([10, 11, 12, 13, 14]), 4)

    def test_streak_breaks_midway(self):
        # 10→11→12 ok (2 higher), 12→11 breaks
        self.assertEqual(M._count_consecutive_higher_lows([10, 11, 12, 11, 13]), 2)

    def test_equal_breaks_streak(self):
        # strict greater-than: equal does NOT count
        self.assertEqual(M._count_consecutive_higher_lows([10, 10, 11]), 0)

    def test_descending_returns_zero(self):
        self.assertEqual(M._count_consecutive_higher_lows([14, 13, 12, 11, 10]), 0)

    def test_single_element_returns_zero(self):
        self.assertEqual(M._count_consecutive_higher_lows([10]), 0)

    def test_empty_returns_zero(self):
        self.assertEqual(M._count_consecutive_higher_lows([]), 0)

    def test_first_pair_higher_only(self):
        # 10→11 ok (1), 11→9 breaks
        self.assertEqual(M._count_consecutive_higher_lows([10, 11, 9, 12, 13]), 1)


if __name__ == "__main__":
    unittest.main()
