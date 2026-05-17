"""Unit tests for core.events pure-logic helpers.

Keyword matching, headline classification, TP/trailing-stop state transitions,
event keys. Network paths (detect_events, check_news_events, SL/TP loop) are
out of scope.
"""

import unittest

import config
import core.events as E


class TestKeywordPattern(unittest.TestCase):
    def test_exact_keyword_fragment(self):
        self.assertEqual(E._kw_to_pattern_part("merger"), r"\bmerger\b")

    def test_stem_keyword_fragment(self):
        self.assertEqual(E._kw_to_pattern_part("prognose*"), r"\bprognose\w*\b")

    def test_pattern_matches_word_boundary(self):
        pat = E._build_keyword_pattern(["merger"])
        self.assertTrue(pat.search("Surprise merger announced"))
        self.assertFalse(pat.search("submergers everywhere"))

    def test_stem_pattern_matches_compounds(self):
        pat = E._build_keyword_pattern(["prognose*"])
        self.assertTrue(pat.search("Prognoseanhebung gemeldet"))
        self.assertTrue(pat.search("Die Prognose steht"))


class TestTickerInTitle(unittest.TestCase):
    def test_bare_ticker_match(self):
        self.assertTrue(E._ticker_in_title("AAPL surges today", "AAPL.DE", None))

    def test_company_name_substring_match(self):
        self.assertTrue(
            E._ticker_in_title("Apple Inc reports revenue", "XYZ.DE", "Apple Inc")
        )

    def test_long_name_token_match(self):
        # ≥4-char token anywhere — guards 2026-05-02 first-token-only bug
        self.assertTrue(
            E._ticker_in_title("Machines firm rallies", "IBM.DE",
                               "International Business Machines")
        )

    def test_no_mention_returns_false(self):
        self.assertFalse(
            E._ticker_in_title("Random market commentary", "AAPL.DE", "Apple Inc")
        )

    def test_short_token_does_not_match(self):
        # "inc" is 3 chars → must not trigger a match on its own
        self.assertFalse(E._ticker_in_title("inc and stuff", "ZZZZ.DE", "Zzzz Inc"))


class TestClassifyHeadline(unittest.TestCase):
    def test_stock_news_open_position_is_high(self):
        ev = E._classify_headline("Apple earnings beat estimates", "AAPL.DE",
                                  is_open_position=True, name="Apple Inc")
        self.assertEqual(ev["type"], "NEWS_STOCK")
        self.assertEqual(ev["priority"], "HIGH")

    def test_stock_news_watchlist_is_medium(self):
        ev = E._classify_headline("Apple earnings beat estimates", "AAPL.DE",
                                  is_open_position=False, name="Apple Inc")
        self.assertEqual(ev["priority"], "MEDIUM")

    def test_rating_change_subtype(self):
        ev = E._classify_headline("Apple upgraded to overweight", "AAPL.DE",
                                  is_open_position=False, name="Apple Inc")
        self.assertEqual(ev["subtype"], "rating_change")

    def test_index_ticker_skipped(self):
        idx = next(iter(config.MARKET_INDICATORS))
        self.assertIsNone(
            E._classify_headline("earnings beat reported", idx,
                                 is_open_position=False, name=None)
        )

    def test_irrelevant_headline_returns_none(self):
        self.assertIsNone(
            E._classify_headline("Unrelated earnings story", "ZZZZ.DE",
                                 is_open_position=False, name="Zzzz Corp")
        )

    def test_geo_commodity_trigger(self):
        if not config.COMMODITY_TRIGGERS:
            self.skipTest("no COMMODITY_TRIGGERS configured")
        _comm, kws = next(iter(config.COMMODITY_TRIGGERS.items()))
        kw = kws[0]
        stem = kw[:-1] if kw.endswith("*") else kw
        ev = E._classify_headline(f"Markt: {stem} im Fokus", "ANYTICKER.DE",
                                  is_open_position=False, name=None)
        self.assertEqual(ev["type"], "NEWS_GEO")
        self.assertEqual(ev["priority"], "HIGH")


class TestTakeProfitHelpers(unittest.TestCase):
    def test_next_tp_scalar(self):
        self.assertEqual(E._next_take_profit({"take_profit": 110}), 110.0)

    def test_next_tp_list_returns_first(self):
        self.assertEqual(E._next_take_profit({"take_profit": [110, 120]}), 110.0)

    def test_next_tp_none_and_empty(self):
        self.assertIsNone(E._next_take_profit({"take_profit": None}))
        self.assertIsNone(E._next_take_profit({"take_profit": []}))

    def test_pop_list_consumes_first(self):
        trade = {"take_profit": [110, 120]}
        E._pop_first_take_profit(trade)
        self.assertEqual(trade["take_profit"], [120])

    def test_pop_last_list_entry_clears_to_none(self):
        trade = {"take_profit": [110]}
        E._pop_first_take_profit(trade)
        self.assertIsNone(trade["take_profit"])

    def test_pop_scalar_clears_to_none(self):
        trade = {"take_profit": 110}
        E._pop_first_take_profit(trade)
        self.assertIsNone(trade["take_profit"])


class TestApplyTrailingStop(unittest.TestCase):
    def test_ratchets_stop_up(self):
        trade = {"trailing_stop_pct": 5, "stop_loss": 90}
        moved = E._apply_trailing_stop(trade, 100)
        self.assertTrue(moved)
        self.assertEqual(trade["stop_loss"], 95.0)  # 100 * (1 - 0.05)

    def test_never_moves_stop_down(self):
        trade = {"trailing_stop_pct": 5, "stop_loss": 95}
        moved = E._apply_trailing_stop(trade, 80)  # candidate 76 < 95
        self.assertFalse(moved)
        self.assertEqual(trade["stop_loss"], 95)

    def test_no_trailing_pct_is_noop(self):
        trade = {"trailing_stop_pct": 0, "stop_loss": 90}
        self.assertFalse(E._apply_trailing_stop(trade, 100))

    def test_seeds_stop_when_absent(self):
        trade = {"trailing_stop_pct": 5, "stop_loss": None}
        moved = E._apply_trailing_stop(trade, 100)
        self.assertTrue(moved)
        self.assertEqual(trade["stop_loss"], 95.0)


class TestEventKey(unittest.TestCase):
    def test_watch_level_hit_key(self):
        ev = {"type": "WATCH_LEVEL_HIT", "ticker": "AAPL", "trigger_price": 150}
        self.assertEqual(E._get_event_key(ev), "watch_AAPL_150")

    def test_watch_invalidated_key(self):
        ev = {"type": "WATCH_INVALIDATED", "ticker": "AAPL", "invalidate_below": 140}
        self.assertEqual(E._get_event_key(ev), "watch_invalid_AAPL_140")


class TestShouldAnalyzeEvents(unittest.TestCase):
    def test_no_events_skips(self):
        ok, _reason = E.should_analyze_events([])
        self.assertFalse(ok)

    def test_high_priority_event_triggers(self):
        ok, reason = E.should_analyze_events([{"priority": "HIGH"}])
        self.assertTrue(ok)
        self.assertIn("high-priority", reason)


if __name__ == "__main__":
    unittest.main()
