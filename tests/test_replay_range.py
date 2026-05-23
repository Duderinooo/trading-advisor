"""Tests for core/replay/replay_range.py — multi-day SL/TP replay."""

import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import patch


def _bar(date_iso, high, low):
    return {"date": date_iso, "open": (high+low)/2, "high": high, "low": low,
            "close": (high+low)/2, "volume": 1_000_000}


class TestReplayRange(unittest.TestCase):
    def test_invalid_range_raises(self):
        from core.replay.replay_range import replay_range
        with self.assertRaises(ValueError):
            replay_range(date(2026, 5, 23), date(2026, 5, 22), {})

    def test_empty_portfolio_walks_with_zero_events(self):
        from core.replay.replay_range import replay_range
        pf = {"open_trades": [], "cash_eur": 500}
        final_pf, result = replay_range(date(2026, 5, 18), date(2026, 5, 22), pf)
        # Mon-Fri 5 weekdays
        self.assertEqual(len(result.days), 5)
        self.assertEqual(result.total_events, 0)
        self.assertEqual(result.cumulative_pnl_eur, 0.0)
        self.assertEqual(result.ending_open_count, 0)

    def test_weekend_days_skipped(self):
        from core.replay.replay_range import replay_range
        pf = {"open_trades": []}
        # Sat-Sun-Mon: only Monday counts.
        final_pf, result = replay_range(date(2026, 5, 23), date(2026, 5, 25), pf)
        self.assertEqual(len(result.days), 1)
        self.assertEqual(result.days[0].date, "2026-05-25")

    def test_xetra_holiday_skipped(self):
        from core.replay.replay_range import replay_range
        pf = {"open_trades": []}
        # 2026-04-03 = Karfreitag in config.XETRA_HOLIDAYS
        final_pf, result = replay_range(
            date(2026, 4, 2), date(2026, 4, 7), pf,  # Thu, Fri(holiday), Sat, Sun, Mon(holiday), Tue
        )
        replayed_dates = {d.date for d in result.days}
        self.assertIn("2026-04-02", replayed_dates)
        self.assertIn("2026-04-07", replayed_dates)
        self.assertNotIn("2026-04-03", replayed_dates)
        self.assertNotIn("2026-04-06", replayed_dates)  # Ostermontag

    def test_sequential_carry_pf_forward(self):
        """Day1 SL hit closes trade → Day2 walks with 0 open trades."""
        from core.replay.replay_range import replay_range
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
                 "stop_loss": 52.0, "take_profit": [55.0]}
        pf = {"open_trades": [trade]}
        bars_by_date = {
            date(2026, 5, 21): _bar("2026-05-21", 53.5, 51.5),  # SL hit
            date(2026, 5, 22): _bar("2026-05-22", 54.0, 53.0),  # no trades open anyway
        }

        def _stub(ticker, target_date, **kwargs):
            return bars_by_date.get(target_date)

        with patch("core.replay.replay_day.get_daily_bar", side_effect=_stub):
            final_pf, result = replay_range(
                date(2026, 5, 21), date(2026, 5, 22), pf,
            )
        self.assertEqual(result.total_closed_full, 1)
        self.assertEqual(result.starting_open_count, 1)
        self.assertEqual(result.ending_open_count, 0)
        # Day 1 should have the event; Day 2 should be empty
        self.assertEqual(len(result.days[0].events), 1)
        self.assertEqual(len(result.days[1].events), 0)

    def test_cumulative_pnl_sums_days(self):
        """SL Day1, then bar on Day2 closes nothing (trade already gone)."""
        from core.replay.replay_range import replay_range
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
                 "stop_loss": 52.0, "take_profit": 55.0}  # scalar tp
        pf = {"open_trades": [trade]}
        bars_by_date = {
            date(2026, 5, 21): _bar("2026-05-21", 55.5, 52.5),  # TP hit (+€20)
            date(2026, 5, 22): _bar("2026-05-22", 55.5, 52.5),
        }

        def _stub(ticker, target_date, **kwargs):
            return bars_by_date.get(target_date)

        with patch("core.replay.replay_day.get_daily_bar", side_effect=_stub):
            final_pf, result = replay_range(
                date(2026, 5, 21), date(2026, 5, 22), pf,
            )
        self.assertAlmostEqual(result.cumulative_pnl_eur, 20.0, places=2)
        self.assertEqual(result.total_closed_full, 1)

    def test_original_portfolio_not_mutated(self):
        from core.replay.replay_range import replay_range
        pf = {"open_trades": [
            {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
             "stop_loss": 52.0, "take_profit": [55.0]},
        ]}
        original = deepcopy(pf)
        with patch(
            "core.replay.replay_day.get_daily_bar",
            return_value=_bar("2026-05-21", 53.5, 51.5),
        ):
            replay_range(date(2026, 5, 21), date(2026, 5, 22), pf)
        self.assertEqual(pf, original)


if __name__ == "__main__":
    unittest.main()
