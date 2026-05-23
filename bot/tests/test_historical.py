"""Tests for core/data/historical.py — daily-bar cache.

Network paths (yfinance fetch) are stubbed. Cache read/write paths exercised
with real tempdir filesystems.
"""

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
            "core.data.historical._CACHE_DIR", self._tmp_path,
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_empty_cache_returns_empty_dict(self):
        from core.data.historical import _load_cache
        self.assertEqual(_load_cache("XYZ"), {})

    def test_round_trip_preserves_bars(self):
        from core.data.historical import _load_cache, _write_cache
        bars = {
            "2026-05-12": {"date": "2026-05-12", "open": 50.0, "high": 51.0,
                           "low": 49.5, "close": 50.8, "volume": 1_000_000},
            "2026-05-13": {"date": "2026-05-13", "open": 50.8, "high": 52.5,
                           "low": 50.6, "close": 52.1, "volume": 1_200_000},
        }
        _write_cache("BAS.DE", bars)
        loaded = _load_cache("BAS.DE")
        self.assertEqual(set(loaded.keys()), set(bars.keys()))
        self.assertEqual(loaded["2026-05-12"]["close"], 50.8)

    def test_cache_sorted_by_date_on_disk(self):
        from core.data.historical import _load_cache, _path_for, _write_cache
        bars = {
            "2026-05-13": {"date": "2026-05-13", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 0},
            "2026-05-12": {"date": "2026-05-12", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 0},
        }
        _write_cache("BAS.DE", bars)
        # Lines on disk must be sorted by date.
        path = _path_for("BAS.DE")
        lines = [json.loads(l) for l in path.read_text().strip().split("\n")]
        self.assertEqual([l["date"] for l in lines], ["2026-05-12", "2026-05-13"])

    def test_ticker_with_slash_escaped_in_filename(self):
        from core.data.historical import _path_for
        path = _path_for("FOO/BAR")
        self.assertNotIn("/", path.name)


class TestGetDailyBarLookup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._patch_dir = patch("core.data.historical._CACHE_DIR", self._tmp_path)
        self._patch_dir.start()

    def tearDown(self):
        self._patch_dir.stop()
        self._tmp.cleanup()

    def test_cached_hit_no_fetch(self):
        from core.data.historical import _write_cache, get_daily_bar
        bars = {"2026-05-12": {"date": "2026-05-12", "open": 50.0,
                                "high": 51.0, "low": 49.5, "close": 50.8,
                                "volume": 1_000_000}}
        _write_cache("BAS.DE", bars)
        with patch("core.data.historical._fetch_yfinance") as mock_fetch:
            bar = get_daily_bar("BAS.DE", date(2026, 5, 12))
            mock_fetch.assert_not_called()
        self.assertIsNotNone(bar)
        self.assertEqual(bar["close"], 50.8)

    def test_cache_miss_auto_fetch_disabled_returns_none(self):
        from core.data.historical import get_daily_bar
        with patch("core.data.historical._fetch_yfinance") as mock_fetch:
            bar = get_daily_bar("BAS.DE", date(2026, 5, 12), auto_fetch=False)
            mock_fetch.assert_not_called()
        self.assertIsNone(bar)

    def test_cache_miss_auto_fetch_pulls_and_persists(self):
        from core.data.historical import _load_cache, get_daily_bar
        fetched = {"2026-05-12": {"date": "2026-05-12", "open": 50.0,
                                   "high": 51.0, "low": 49.5, "close": 50.8,
                                   "volume": 1_000_000}}
        with patch("core.data.historical._fetch_yfinance", return_value=fetched):
            bar = get_daily_bar("BAS.DE", date(2026, 5, 12))
        self.assertIsNotNone(bar)
        # Persisted for next call
        self.assertIn("2026-05-12", _load_cache("BAS.DE"))


class TestRangeQuery(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._patch_dir = patch("core.data.historical._CACHE_DIR", self._tmp_path)
        self._patch_dir.start()

    def tearDown(self):
        self._patch_dir.stop()
        self._tmp.cleanup()

    def test_range_returns_sorted(self):
        from core.data.historical import _write_cache, get_daily_bars_range
        bars = {
            f"2026-05-{day:02d}": {"date": f"2026-05-{day:02d}", "open": 50,
                                    "high": 51, "low": 49, "close": 50,
                                    "volume": 1_000_000}
            for day in (11, 12, 13, 14, 15)
        }
        _write_cache("BAS.DE", bars)
        with patch("core.data.historical._fetch_yfinance") as mock_fetch:
            result = get_daily_bars_range(
                "BAS.DE", date(2026, 5, 12), date(2026, 5, 14),
            )
            # Cache covers expected window — should NOT re-fetch
            mock_fetch.assert_not_called()
        self.assertEqual([r["date"] for r in result],
                         ["2026-05-12", "2026-05-13", "2026-05-14"])


if __name__ == "__main__":
    unittest.main()
