"""Tests for deterministic proposed-trade enrichment (pure, no network)."""

import unittest

import config
from core.proposals import enrich_proposed_trade


def _snap(price=50.0, atr_pct=2.0, **extra):
    d = {"price": price, "atr14_pct": atr_pct}
    d.update(extra)
    return d


class EnrichProposalTest(unittest.TestCase):
    def test_none_without_price_or_atr(self):
        self.assertIsNone(
            enrich_proposed_trade({"ticker": "X"}, {"atr14_pct": 2.0}, 400, 1000))
        self.assertIsNone(
            enrich_proposed_trade({"ticker": "X"}, {"price": 50.0}, 400, 1000))
        self.assertIsNone(
            enrich_proposed_trade({"ticker": "X"}, {"price": 50.0, "atr14_pct": 0}, 400, 1000))

    def test_entry_from_trigger_invalidate_stop_and_tp_ladder(self):
        lvl = {"ticker": "X", "type": "support_bounce", "trigger_price": 48.0,
               "invalidate_below": 46.5, "thesis": "t"}
        p = enrich_proposed_trade(lvl, _snap(price=49, atr_pct=2.0), 400, 1000)
        self.assertEqual(p["entry"], 48.0)
        self.assertEqual(p["stop_loss"], 46.5)
        # rps = 1.5 → tp1 = 48 + 2*1.5 = 51.0, tp2 = 48 + 3*1.5 = 52.5
        self.assertEqual(p["take_profit"], [51.0, 52.5])
        self.assertEqual(p["tp_r"], [2.0, 3.0])

    def test_default_stop_when_no_invalidate(self):
        # atr = 50*2/100 = 1.0 → default 1.5×ATR stop, inside the sanity band.
        lvl = {"ticker": "X", "type": "mean_reversion", "trigger_price": 50.0, "thesis": "t"}
        p = enrich_proposed_trade(lvl, _snap(price=50, atr_pct=2.0), 400, 1000)
        self.assertEqual(p["stop_loss"], 48.5)
        self.assertIsNone(p["sl_clamped"])

    def test_too_tight_stop_widened_to_floor(self):
        lvl = {"ticker": "X", "type": "support_bounce", "trigger_price": 50.0,
               "invalidate_below": 49.8, "thesis": "t"}
        p = enrich_proposed_trade(lvl, _snap(price=50, atr_pct=2.0), 400, 1000)
        self.assertEqual(p["sl_clamped"], "widened")
        self.assertAlmostEqual(p["stop_loss"], 50.0 - config.MIN_SL_DISTANCE_ATR * 1.0, places=2)

    def test_too_wide_stop_tightened_to_ceiling(self):
        lvl = {"ticker": "X", "type": "support_bounce", "trigger_price": 50.0,
               "invalidate_below": 45.0, "thesis": "t"}
        p = enrich_proposed_trade(lvl, _snap(price=50, atr_pct=2.0), 400, 1000)
        self.assertEqual(p["sl_clamped"], "tightened")
        self.assertAlmostEqual(p["stop_loss"], 50.0 - config.MAX_SL_DISTANCE_ATR * 1.0, places=2)

    def test_unaffordable_flag(self):
        lvl = {"ticker": "X", "type": "breakout_long", "trigger_price": 150.0, "thesis": "t"}
        p = enrich_proposed_trade(lvl, _snap(price=150, atr_pct=2.0), 400, 1000)
        self.assertFalse(p["gates"]["affordable"])

    def test_shares_int_signals_passthrough(self):
        lvl = {"ticker": "X", "type": "support_bounce", "trigger_price": 48.0,
               "invalidate_below": 46.5, "thesis": "t"}
        p = enrich_proposed_trade(
            lvl, _snap(price=48, atr_pct=2.2, rsi14=34, base_quality_score=7,
                       higher_lows_5d=4, wk_trend="MIXED"),
            400, 1000)
        self.assertIsInstance(p["shares"], int)
        self.assertGreaterEqual(p["shares"], 0)
        self.assertEqual(p["gates"]["whole_share_ok"], p["shares"] >= 1)
        self.assertEqual(p["signals"]["base_quality_score"], 7)
        self.assertEqual(p["signals"]["rsi14"], 34)
        self.assertIn("fee_ok", p["gates"])

    def test_zone_midpoint_entry_fallback(self):
        lvl = {"ticker": "X", "type": "accumulation_zone", "zone_low": 40.0,
               "zone_high": 42.0, "thesis": "t"}
        p = enrich_proposed_trade(lvl, _snap(price=41, atr_pct=2.0), 400, 1000)
        self.assertEqual(p["entry"], 41.0)  # (40+42)/2


if __name__ == "__main__":
    unittest.main()
