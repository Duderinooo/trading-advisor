"""Unit tests for core.livefeed HTML/number parsing helpers.

Network functions (get_live_quote, _resolve_instrument_id) are out of scope —
only the deterministic parsers are covered here.
"""

import unittest

import core.data.livefeed as L


class TestParseDeNumber(unittest.TestCase):
    def test_simple_german_decimal(self):
        self.assertEqual(L._parse_de_number("60,2900"), 60.29)

    def test_thousands_separator(self):
        self.assertEqual(L._parse_de_number("1.234,56"), 1234.56)

    def test_strips_percent_and_plus(self):
        self.assertEqual(L._parse_de_number("+1,5%"), 1.5)

    def test_strips_euro_sign(self):
        self.assertEqual(L._parse_de_number("€100,00"), 100.0)

    def test_none_returns_none(self):
        self.assertIsNone(L._parse_de_number(None))

    def test_empty_returns_none(self):
        self.assertIsNone(L._parse_de_number(""))

    def test_garbage_returns_none(self):
        self.assertIsNone(L._parse_de_number("not a number"))


class TestExtractQuoteFields(unittest.TestCase):
    HTML = (
        '<html><body>'
        '<span source="lightstreamer" field="mid">148,04</span>'
        '<span source="lightstreamer" field="bid">147,68</span>'
        '<span source="lightstreamer" field="ask">148,40</span>'
        '<span field="ignored">should not appear</span>'
        '</body></html>'
    )

    def test_extracts_lightstreamer_spans(self):
        fields = L._extract_quote_fields(self.HTML)
        self.assertEqual(fields["mid"], "148,04")
        self.assertEqual(fields["bid"], "147,68")
        self.assertEqual(fields["ask"], "148,40")

    def test_non_lightstreamer_spans_ignored(self):
        fields = L._extract_quote_fields(self.HTML)
        self.assertNotIn("ignored", fields)

    def test_empty_html_yields_empty_dict(self):
        self.assertEqual(L._extract_quote_fields("<html></html>"), {})


class TestMarketStatus(unittest.TestCase):
    def test_open_badge(self):
        html = '<div><b>Status:</b><span class="btn">open</span></div>'
        self.assertEqual(L._market_status(html), "open")

    def test_closed_badge(self):
        html = '<div><b>Status:</b><span class="btn">closed</span></div>'
        self.assertEqual(L._market_status(html), "closed")

    def test_missing_badge_returns_none(self):
        self.assertIsNone(L._market_status("<div>no status here</div>"))


if __name__ == "__main__":
    unittest.main()
