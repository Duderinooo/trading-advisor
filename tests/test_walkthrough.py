"""Tests for core/replay/walkthrough.py — single-trade bar walker."""

import unittest
from datetime import date
from unittest.mock import patch


class TestWalkTradeOutcomes(unittest.TestCase):
    """Mock get_daily_bars_range with synthetic bar sequences to verify
    walk_trade resolves win / loss / still_open / ambiguous correctly."""

    def _bars(self, *triples) -> list[dict]:
        """triples: (date_iso, high, low). open=close=mid for simplicity."""
        return [
            {"date": d, "open": (h + l) / 2, "high": h, "low": l,
             "close": (h + l) / 2, "volume": 1_000_000}
            for d, h, l in triples
        ]

    def test_tp_first_returns_win(self):
        from core.replay.walkthrough import walk_trade
        bars = self._bars(
            ("2026-05-13", 53.5, 52.5),  # bar1: no touch
            ("2026-05-14", 55.5, 52.5),  # bar2: TP touched (high≥55)
        )
        with patch("core.replay.walkthrough.get_daily_bars_range", return_value=bars):
            r = walk_trade("BAS.DE", date(2026, 5, 12), 53.0, 52.0, 55.0)
        self.assertEqual(r.final_outcome, "win")
        self.assertTrue(r.hit_tp1)
        self.assertFalse(r.hit_sl)
        self.assertEqual(r.bars_walked, 2)
        self.assertAlmostEqual(r.realized_r, 2.0, places=2)  # (55-53)/(53-52)

    def test_sl_first_returns_loss(self):
        from core.replay.walkthrough import walk_trade
        bars = self._bars(
            ("2026-05-13", 53.5, 51.5),  # bar1: SL touched (low≤52)
            ("2026-05-14", 56.0, 53.5),  # never reached, walk stops at bar1
        )
        with patch("core.replay.walkthrough.get_daily_bars_range", return_value=bars):
            r = walk_trade("BAS.DE", date(2026, 5, 12), 53.0, 52.0, 55.0)
        self.assertEqual(r.final_outcome, "loss")
        self.assertTrue(r.hit_sl)
        self.assertFalse(r.hit_tp1)
        self.assertEqual(r.bars_walked, 1)
        self.assertAlmostEqual(r.realized_r, -1.0, places=2)

    def test_both_touched_returns_ambiguous(self):
        from core.replay.walkthrough import walk_trade
        bars = self._bars(
            ("2026-05-13", 55.5, 51.5),  # both touched in same bar
        )
        with patch("core.replay.walkthrough.get_daily_bars_range", return_value=bars):
            r = walk_trade("BAS.DE", date(2026, 5, 12), 53.0, 52.0, 55.0)
        self.assertEqual(r.final_outcome, "ambiguous")
        self.assertTrue(r.hit_sl)
        self.assertTrue(r.hit_tp1)
        self.assertIsNone(r.realized_r)

    def test_no_touch_returns_still_open(self):
        from core.replay.walkthrough import walk_trade
        bars = self._bars(
            ("2026-05-13", 53.3, 52.5),
            ("2026-05-14", 53.5, 52.7),
            ("2026-05-15", 53.8, 52.9),
        )
        with patch("core.replay.walkthrough.get_daily_bars_range", return_value=bars):
            r = walk_trade("BAS.DE", date(2026, 5, 12), 53.0, 52.0, 55.0)
        self.assertEqual(r.final_outcome, "still_open")
        self.assertFalse(r.hit_sl)
        self.assertFalse(r.hit_tp1)
        self.assertIsNone(r.realized_r)
        self.assertEqual(r.bars_walked, 3)

    def test_mfe_mae_tracked_across_walk(self):
        from core.replay.walkthrough import walk_trade
        bars = self._bars(
            ("2026-05-13", 54.0, 52.5),  # mfe=54, trough=52.5
            ("2026-05-14", 54.8, 52.2),  # mfe=54.8, trough=52.2
            ("2026-05-15", 55.5, 52.7),  # tp touched
        )
        with patch("core.replay.walkthrough.get_daily_bars_range", return_value=bars):
            r = walk_trade("BAS.DE", date(2026, 5, 12), 53.0, 52.0, 55.0)
        self.assertEqual(r.mfe_price, 55.5)  # high on the resolution bar counts
        self.assertEqual(r.mae_price, 52.2)
        self.assertGreater(r.peak_pct, 4.5)
        self.assertLess(r.trough_pct, -1.4)

    def test_invalid_long_params_raises(self):
        from core.replay.walkthrough import walk_trade
        with self.assertRaises(ValueError):
            walk_trade("BAS.DE", date(2026, 5, 12), 50.0, 52.0, 55.0)  # sl > entry
        with self.assertRaises(ValueError):
            walk_trade("BAS.DE", date(2026, 5, 12), 53.0, 52.0, 52.5)  # tp < entry

    def test_no_bars_returns_none(self):
        from core.replay.walkthrough import walk_trade
        with patch("core.replay.walkthrough.get_daily_bars_range", return_value=[]):
            r = walk_trade("BAS.DE", date(2026, 5, 12), 53.0, 52.0, 55.0)
        self.assertIsNone(r)


if __name__ == "__main__":
    unittest.main()
