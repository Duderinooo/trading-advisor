"""Tests for core/replay/compare.py — config-override parameter sweep."""

import unittest
from datetime import date
from unittest.mock import patch

import config


def _bar(date_iso, high, low):
    return {"date": date_iso, "open": (high+low)/2, "high": high, "low": low,
            "close": (high+low)/2, "volume": 1_000_000}


class TestCoerce(unittest.TestCase):
    def test_int(self):
        from core.replay.compare import _coerce
        self.assertEqual(_coerce("42"), 42)
        self.assertIsInstance(_coerce("42"), int)

    def test_float(self):
        from core.replay.compare import _coerce
        self.assertEqual(_coerce("0.5"), 0.5)
        self.assertEqual(_coerce("1e3"), 1000.0)

    def test_str_fallback(self):
        from core.replay.compare import _coerce
        self.assertEqual(_coerce("hello"), "hello")


class TestReplayWithOverrides(unittest.TestCase):
    def test_unknown_attribute_raises(self):
        from core.replay.compare import replay_with_overrides
        with self.assertRaises(AttributeError):
            replay_with_overrides(
                {"DEFINITELY_NOT_A_REAL_CONFIG_NAME": 99},
                date(2026, 5, 21), date(2026, 5, 21), {"open_trades": []},
            )

    def test_overrides_restored_after_call(self):
        from core.replay.compare import replay_with_overrides
        original = config.PARTIAL_TP_FRACTION
        try:
            replay_with_overrides(
                {"PARTIAL_TP_FRACTION": 0.99},
                date(2026, 5, 21), date(2026, 5, 21), {"open_trades": []},
            )
        except Exception:
            pass
        self.assertEqual(config.PARTIAL_TP_FRACTION, original)

    def test_overrides_restored_even_on_error(self):
        from core.replay.compare import replay_with_overrides
        original = config.PARTIAL_TP_FRACTION
        # Force inner failure by patching replay_range.
        with patch(
            "core.replay.compare.replay_range", side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                replay_with_overrides(
                    {"PARTIAL_TP_FRACTION": 0.99},
                    date(2026, 5, 21), date(2026, 5, 21), {"open_trades": []},
                )
        self.assertEqual(config.PARTIAL_TP_FRACTION, original)

    def test_override_actually_applied_during_call(self):
        """Variant w/ PARTIAL_TP_FRACTION=0.99 sells more @ TP1 than baseline=0.5."""
        from core.replay.compare import replay_with_overrides
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10.0,
                 "stop_loss": 52.0, "take_profit": [55.0, 57.0]}
        pf = {"open_trades": [trade]}
        bar = _bar("2026-05-21", 55.5, 52.5)  # TP1 hit

        with patch("core.replay.replay_day.get_daily_bar", return_value=bar):
            # Baseline run (default 0.5)
            _, baseline = replay_with_overrides(
                {"PARTIAL_TP_FRACTION": 0.5},
                date(2026, 5, 21), date(2026, 5, 21), pf,
            )
            # Variant (0.9 → larger partial)
            _, variant = replay_with_overrides(
                {"PARTIAL_TP_FRACTION": 0.9},
                date(2026, 5, 21), date(2026, 5, 21), pf,
            )
        # Both close partial at TP1
        self.assertEqual(baseline.total_closed_partial, 1)
        self.assertEqual(variant.total_closed_partial, 1)
        # Variant takes more profit at TP1 (90% × €2 vs 50% × €2)
        self.assertGreater(variant.cumulative_pnl_eur, baseline.cumulative_pnl_eur)


class TestCompareToBaseline(unittest.TestCase):
    def test_delta_zero_when_override_doesnt_apply(self):
        """If no trades open in range, override has no effect on P&L."""
        from core.replay.compare import compare_to_baseline
        result = compare_to_baseline(
            {"PARTIAL_TP_FRACTION": 0.99},
            date(2026, 5, 21), date(2026, 5, 21),
            {"open_trades": []},
        )
        self.assertEqual(result["delta"]["pnl_eur"], 0.0)
        self.assertEqual(result["baseline"]["cumulative_pnl_eur"],
                         result["variant"]["cumulative_pnl_eur"])

    def test_delta_positive_when_variant_profits_more(self):
        from core.replay.compare import compare_to_baseline
        trade = {"ticker": "BAS.DE", "entry_price": 53.0, "shares": 10.0,
                 "stop_loss": 52.0, "take_profit": [55.0, 57.0]}
        pf = {"open_trades": [trade]}
        bar = _bar("2026-05-21", 55.5, 52.5)
        with patch("core.replay.replay_day.get_daily_bar", return_value=bar):
            result = compare_to_baseline(
                {"PARTIAL_TP_FRACTION": 0.9},
                date(2026, 5, 21), date(2026, 5, 21), pf,
            )
        self.assertGreater(result["delta"]["pnl_eur"], 0)


if __name__ == "__main__":
    unittest.main()
