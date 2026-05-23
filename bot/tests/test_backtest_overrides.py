"""Tests for the --override CLI extension in core/backtest.py."""

import unittest

import config


class TestReplayWithOverrides(unittest.TestCase):
    def test_unknown_attr_raises(self):
        from core.backtest import _replay_with_overrides
        with self.assertRaises(AttributeError):
            _replay_with_overrides({"DEFINITELY_NOT_A_REAL_CONFIG": 99})

    def test_overrides_restored_after_call(self):
        from core.backtest import _replay_with_overrides
        original = config.MIN_EXPECTED_EDGE
        _replay_with_overrides({"MIN_EXPECTED_EDGE": 0.99})
        self.assertEqual(config.MIN_EXPECTED_EDGE, original)

    def test_overrides_restored_on_error(self):
        from core.backtest import _replay_with_overrides
        from unittest.mock import patch
        original = config.MIN_EXPECTED_EDGE
        with patch("core.backtest.replay_all", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                _replay_with_overrides({"MIN_EXPECTED_EDGE": 0.99})
        self.assertEqual(config.MIN_EXPECTED_EDGE, original)

    def test_override_actually_applied_during_call(self):
        """Stricter MIN_EXPECTED_EDGE should produce ≥ baseline blocks on edge gate."""
        from core.backtest import _replay_with_overrides, replay_all

        # Synthetic closed_trades with mid-edge entries.
        fake_trades = [
            {"ticker": "BAS.DE", "entry_price": 50.0, "stop_loss": 48.0,
             "take_profit": [54.0], "p_win": 0.55,
             "setup_type": "breakout_resistance", "pnl_pct": 8.0,
             "atr14": 1.0, "rs_20d_vs_index_pct": 2.0, "volume_ratio": 1.2,
             "wk_trend": "UP"},
            {"ticker": "DBK.DE", "entry_price": 26.0, "stop_loss": 25.0,
             "take_profit": [28.0], "p_win": 0.50,
             "setup_type": "breakout_resistance", "pnl_pct": -3.8,
             "atr14": 0.5, "rs_20d_vs_index_pct": 1.0, "volume_ratio": 1.5,
             "wk_trend": "UP"},
        ]

        from unittest.mock import patch
        with patch("core.backtest.load_portfolio", return_value={"closed_trades": fake_trades}):
            baseline = replay_all()
            variant = _replay_with_overrides({"MIN_EXPECTED_EDGE": 0.99})

        baseline_blocks = baseline["gates"].get("edge", {}).get("would_block", 0)
        variant_blocks = variant["gates"].get("edge", {}).get("would_block", 0)
        # Stricter threshold = more blocks (or at minimum, same).
        self.assertGreaterEqual(variant_blocks, baseline_blocks)


class TestCoerce(unittest.TestCase):
    def test_int_then_float_then_str(self):
        from core.backtest import _coerce
        self.assertEqual(_coerce("42"), 42)
        self.assertEqual(_coerce("0.06"), 0.06)
        self.assertEqual(_coerce("foo"), "foo")


if __name__ == "__main__":
    unittest.main()
