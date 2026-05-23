"""Tests for core/replay/watch_level_replay.py."""

import unittest
from datetime import date
from unittest.mock import patch


def _bar(date_iso, high, low, close=None):
    return {"date": date_iso, "open": (high+low)/2, "high": high, "low": low,
            "close": close if close is not None else (high+low)/2,
            "volume": 1_000_000}


class TestLineMode(unittest.TestCase):
    def test_proximity_hit_on_first_bar(self):
        from core.replay.watch_level_replay import replay_watch_level
        bars = [_bar("2026-05-13", 53.55, 53.10, 53.40)]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="breakout_long",
            )
        self.assertTrue(r.triggered)
        self.assertEqual(r.reason, "proximity_line")
        self.assertEqual(r.hit_date, "2026-05-13")
        self.assertEqual(r.bars_walked, 1)

    def test_no_proximity_no_hit(self):
        from core.replay.watch_level_replay import replay_watch_level
        # All bars far below trigger
        bars = [_bar(f"2026-05-{d}", 51.5, 50.5) for d in (13, 14, 15)]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="breakout_long",
                window_days=3,
            )
        self.assertFalse(r.triggered)
        self.assertEqual(r.reason, "no_hit")
        self.assertEqual(r.bars_walked, 3)

    def test_long_breakout_high_below_trigger_doesnt_hit(self):
        """high < trigger fails direction-aware check even within proximity %."""
        from core.replay.watch_level_replay import replay_watch_level
        # Proximity ≤ 1% but high < trigger
        bars = [_bar("2026-05-13", 53.20, 52.50)]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="breakout_long",
            )
        self.assertFalse(r.triggered)


class TestConfirmCloseAbove(unittest.TestCase):
    def test_close_below_confirm_no_hit(self):
        from core.replay.watch_level_replay import replay_watch_level
        # Proximity OK, high above trigger, but close < confirm_close_above
        bars = [_bar("2026-05-13", 53.60, 53.10, close=53.40)]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="breakout_long",
                confirm_close_above=53.70,
            )
        self.assertFalse(r.triggered)

    def test_close_above_confirm_hits(self):
        from core.replay.watch_level_replay import replay_watch_level
        bars = [_bar("2026-05-13", 53.90, 53.10, close=53.80)]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="breakout_long",
                confirm_close_above=53.70,
            )
        self.assertTrue(r.triggered)
        self.assertEqual(r.reason, "confirm_close")


class TestZoneMode(unittest.TestCase):
    def test_close_inside_zone_hits(self):
        from core.replay.watch_level_replay import replay_watch_level
        bars = [_bar("2026-05-13", 54.00, 53.40, close=53.80)]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="accumulation_zone",
                zone_low=53.50, zone_high=54.50,
            )
        self.assertTrue(r.triggered)
        self.assertEqual(r.reason, "zone_entry")

    def test_bar_straddles_zone_hits(self):
        """high above zone_low + low below zone_high → bar straddles zone."""
        from core.replay.watch_level_replay import replay_watch_level
        bars = [_bar("2026-05-13", 55.0, 52.5, close=54.5)]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="accumulation_zone",
                zone_low=53.0, zone_high=54.0,
            )
        self.assertTrue(r.triggered)

    def test_zone_no_overlap_no_hit(self):
        from core.replay.watch_level_replay import replay_watch_level
        bars = [_bar("2026-05-13", 51.0, 50.5, close=50.8)]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="accumulation_zone",
                zone_low=53.0, zone_high=54.0,
            )
        self.assertFalse(r.triggered)

    def test_zone_invalid_low_ge_high_falls_to_line(self):
        from core.replay.watch_level_replay import replay_watch_level
        # zone_low ≥ zone_high → zone_mode disabled, falls back to line-mode
        bars = [_bar("2026-05-13", 51.0, 50.5)]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="breakout_long",
                zone_low=54.0, zone_high=53.0,  # inverted = invalid
            )
        self.assertFalse(r.triggered)  # line-mode: proximity fail


class TestEdgeCases(unittest.TestCase):
    def test_no_bars_returns_no_data(self):
        from core.replay.watch_level_replay import replay_watch_level
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=[],
        ):
            r = replay_watch_level(
                "ZZZ.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="breakout_long",
            )
        self.assertFalse(r.triggered)
        self.assertEqual(r.reason, "no_data")
        self.assertEqual(r.bars_walked, 0)

    def test_first_bar_doesnt_hit_walks_to_second(self):
        from core.replay.watch_level_replay import replay_watch_level
        bars = [
            _bar("2026-05-13", 52.0, 51.5, close=51.8),       # too low
            _bar("2026-05-14", 53.55, 52.80, close=53.40),    # hits
        ]
        with patch(
            "core.replay.watch_level_replay.get_daily_bars_range",
            return_value=bars,
        ):
            r = replay_watch_level(
                "BAS.DE", date(2026, 5, 13),
                trigger_price=53.50, level_type="breakout_long",
                window_days=5,
            )
        self.assertTrue(r.triggered)
        self.assertEqual(r.hit_date, "2026-05-14")
        self.assertEqual(r.bars_walked, 2)


if __name__ == "__main__":
    unittest.main()
