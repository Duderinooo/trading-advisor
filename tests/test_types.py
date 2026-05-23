"""Tests for core/types.py — TypedDict schemas + namespace export."""

import unittest


class TestTypedDictUsage(unittest.TestCase):
    """TypedDict is structural; instances are plain dicts. Tests ensure the
    schema names are importable + the docstring contract holds at runtime."""

    def test_imports(self):
        from core.types import (
            CashMovement, ClosedTrade, Recommendation,
            Trade, TriggeredEvent, WatchLevel,
        )
        # All are TypedDicts → callable, return dicts.
        self.assertIsInstance(Trade(ticker="BAS.DE", entry_price=53.0), dict)
        self.assertIsInstance(WatchLevel(ticker="BAS.DE", type="breakout_long"), dict)
        self.assertIsInstance(Recommendation(kind="entry", ticker="X"), dict)

    def test_re_exported_via_core_namespace(self):
        import core
        self.assertTrue(hasattr(core, "Trade"))
        self.assertTrue(hasattr(core, "Recommendation"))
        self.assertTrue(hasattr(core, "WatchLevel"))
        self.assertTrue(hasattr(core, "CashMovement"))
        self.assertTrue(hasattr(core, "ClosedTrade"))

    def test_closed_trade_inherits_trade_keys(self):
        """ClosedTrade extends Trade with exit_* fields."""
        from core.types import ClosedTrade, Trade
        trade_keys = set(Trade.__annotations__.keys())
        closed_keys = set(ClosedTrade.__annotations__.keys())
        # ClosedTrade.__annotations__ shows only its OWN fields (not inherited)
        # but instantiation should accept Trade fields too.
        c = ClosedTrade(ticker="BAS.DE", entry_price=53.0, exit_price=55.0)
        self.assertEqual(c["exit_price"], 55.0)
        self.assertEqual(c["ticker"], "BAS.DE")
        # Sanity: ClosedTrade defines AT LEAST the exit fields
        self.assertIn("exit_price", closed_keys)
        self.assertIn("exit_date", closed_keys)
        self.assertIn("pnl_eur", closed_keys)

    def test_total_false_allows_partial(self):
        """All schemas use total=False → partial instances valid."""
        from core.types import Trade
        t = Trade(ticker="BAS.DE")  # most fields missing OK
        self.assertEqual(t["ticker"], "BAS.DE")

    def test_runtime_is_just_a_dict(self):
        """TypedDict has zero runtime overhead — typed dicts ARE dicts."""
        from core.types import Trade
        t = Trade(ticker="BAS.DE", entry_price=53.0)
        # Can mutate as normal dict
        t["extra_field"] = "anything"  # no enforcement
        self.assertEqual(t["extra_field"], "anything")
        self.assertIs(type(t), dict)


if __name__ == "__main__":
    unittest.main()
