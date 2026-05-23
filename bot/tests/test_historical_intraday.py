"""Tests for core/data/historical_intraday.py — 15min bar cache + first-touch."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


class TestCacheReadWrite(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._patch = patch(
            "core.data.historical_intraday._CACHE_DIR", self._tmp_path,
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_round_trip_sorted_by_ts(self):
        from core.data.historical_intraday import _load_cache, _path_for, _write_cache
        bars = {
            "2026-05-21 10:15": {"ts": "2026-05-21 10:15", "date": "2026-05-21",
                                  "open": 50, "high": 51, "low": 49,
                                  "close": 50.5, "volume": 1000},
            "2026-05-21 09:30": {"ts": "2026-05-21 09:30", "date": "2026-05-21",
                                  "open": 50.5, "high": 50.8, "low": 50.0,
                                  "close": 50.2, "volume": 1500},
        }
        _write_cache("BAS.DE", bars)
        path = _path_for("BAS.DE")
        lines = [json.loads(l) for l in path.read_text().strip().split("\n")]
        # Sorted ascending by ts on disk
        self.assertEqual([l["ts"] for l in lines],
                         ["2026-05-21 09:30", "2026-05-21 10:15"])
        # Reload preserves all
        self.assertEqual(set(_load_cache("BAS.DE").keys()), set(bars.keys()))


class TestGetIntradayBars(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._patch = patch(
            "core.data.historical_intraday._CACHE_DIR", self._tmp_path,
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_cached_hit_no_fetch(self):
        from core.data.historical_intraday import _write_cache, get_intraday_bars
        bars = {
            f"2026-05-21 {h:02d}:30": {
                "ts": f"2026-05-21 {h:02d}:30", "date": "2026-05-21",
                "open": 50, "high": 51, "low": 49, "close": 50, "volume": 1000,
            }
            for h in range(9, 11)
        }
        _write_cache("BAS.DE", bars)
        with patch(
            "core.data.historical_intraday._fetch_yfinance_intraday",
        ) as mock_fetch:
            out = get_intraday_bars("BAS.DE", date(2026, 5, 21))
            mock_fetch.assert_not_called()
        self.assertEqual(len(out), 2)
        # Sorted ascending
        self.assertLess(out[0]["ts"], out[1]["ts"])

    def test_cache_miss_auto_fetch(self):
        from core.data.historical_intraday import get_intraday_bars
        fetched = {
            "2026-05-21 09:30": {
                "ts": "2026-05-21 09:30", "date": "2026-05-21",
                "open": 50, "high": 51, "low": 49, "close": 50, "volume": 1000,
            },
        }
        with patch(
            "core.data.historical_intraday._fetch_yfinance_intraday",
            return_value=fetched,
        ):
            out = get_intraday_bars("BAS.DE", date(2026, 5, 21))
        self.assertEqual(len(out), 1)

    def test_yfinance_failure_returns_empty(self):
        from core.data.historical_intraday import get_intraday_bars
        with patch(
            "core.data.historical_intraday._fetch_yfinance_intraday",
            return_value={},
        ):
            out = get_intraday_bars("BAS.DE", date(2026, 5, 21))
        self.assertEqual(out, [])


class TestFirstTouchIntraday(unittest.TestCase):
    def _bars(self, *triples):
        """triples: (ts, high, low)."""
        return [
            {"ts": ts, "date": ts.split(" ")[0], "open": (h+l)/2,
             "high": h, "low": l, "close": (h+l)/2, "volume": 1000}
            for ts, h, l in triples
        ]

    def test_sl_first(self):
        from core.data.historical_intraday import first_touch_intraday
        bars = self._bars(
            ("2026-05-21 09:30", 53.4, 52.5),  # neither
            ("2026-05-21 09:45", 53.2, 51.8),  # SL hit (low ≤ 52)
            ("2026-05-21 10:00", 55.5, 52.0),  # TP would hit but already SL
        )
        with patch(
            "core.data.historical_intraday.get_intraday_bars", return_value=bars,
        ):
            r = first_touch_intraday("BAS.DE", date(2026, 5, 21), 52.0, 55.0)
        self.assertEqual(r, "sl")

    def test_tp_first(self):
        from core.data.historical_intraday import first_touch_intraday
        bars = self._bars(
            ("2026-05-21 09:30", 53.4, 52.5),
            ("2026-05-21 09:45", 55.5, 52.5),  # TP hit
            ("2026-05-21 10:00", 55.0, 51.5),
        )
        with patch(
            "core.data.historical_intraday.get_intraday_bars", return_value=bars,
        ):
            r = first_touch_intraday("BAS.DE", date(2026, 5, 21), 52.0, 55.0)
        self.assertEqual(r, "tp")

    def test_neither_touched_returns_none(self):
        from core.data.historical_intraday import first_touch_intraday
        bars = self._bars(
            ("2026-05-21 09:30", 53.4, 52.5),
            ("2026-05-21 09:45", 53.5, 52.6),
        )
        with patch(
            "core.data.historical_intraday.get_intraday_bars", return_value=bars,
        ):
            r = first_touch_intraday("BAS.DE", date(2026, 5, 21), 52.0, 55.0)
        self.assertIsNone(r)

    def test_same_bar_still_ambiguous(self):
        from core.data.historical_intraday import first_touch_intraday
        # Both hit in the same 15min bar — still ambiguous.
        bars = self._bars(
            ("2026-05-21 09:30", 53.4, 52.5),
            ("2026-05-21 09:45", 55.5, 51.5),  # both touch in this bar
        )
        with patch(
            "core.data.historical_intraday.get_intraday_bars", return_value=bars,
        ):
            r = first_touch_intraday("BAS.DE", date(2026, 5, 21), 52.0, 55.0)
        self.assertIsNone(r)

    def test_no_bars_returns_none(self):
        from core.data.historical_intraday import first_touch_intraday
        with patch(
            "core.data.historical_intraday.get_intraday_bars", return_value=[],
        ):
            r = first_touch_intraday("BAS.DE", date(2026, 5, 21), 52.0, 55.0)
        self.assertIsNone(r)


class TestReplayDayUsesIntradayOnAmbiguous(unittest.TestCase):
    def test_ambiguous_daily_resolved_by_intraday_tp(self):
        from core.replay.replay_day import replay_sltp_step
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
                 "stop_loss": 52.0, "take_profit": [55.0]}
        pf = {"open_trades": [trade]}
        daily_bar = {"date": "2026-05-21", "open": 53, "high": 55.5,
                     "low": 51.5, "close": 53, "volume": 1000}
        with patch(
            "core.replay.replay_day.get_daily_bar", return_value=daily_bar,
        ), patch(
            "core.replay.replay_day.first_touch_intraday", return_value="tp",
        ):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 21))
        # Resolved as TAKE_PROFIT_HIT, NOT AMBIGUOUS.
        self.assertEqual(result.ambiguous, 0)
        self.assertEqual(result.closed_full, 1)
        self.assertEqual(result.events[0]["type"], "TAKE_PROFIT_HIT")

    def test_intraday_unavailable_falls_back_to_ambiguous(self):
        from core.replay.replay_day import replay_sltp_step
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10,
                 "stop_loss": 52.0, "take_profit": [55.0]}
        pf = {"open_trades": [trade]}
        daily_bar = {"date": "2026-05-21", "open": 53, "high": 55.5,
                     "low": 51.5, "close": 53, "volume": 1000}
        with patch(
            "core.replay.replay_day.get_daily_bar", return_value=daily_bar,
        ), patch(
            "core.replay.replay_day.first_touch_intraday", return_value=None,
        ):
            out_pf, result = replay_sltp_step(pf, date(2026, 5, 21))
        self.assertEqual(result.ambiguous, 1)
        self.assertEqual(result.closed_full, 0)


if __name__ == "__main__":
    unittest.main()
