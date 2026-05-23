"""Unit tests for core.data.news_rss helpers.

Network is monkeypatched — these tests never touch the real Google
News feed. They verify URL construction (locale + topic routing),
parse-layer behaviour (cutoff filtering, missing fields), and error
tolerance (HTTP errors → empty list, never raises).
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.data import news_rss


def _struct_for(dt: datetime):
    """Convert a tz-aware datetime to the 9-tuple time.struct_time form
    feedparser hands back via `published_parsed`."""
    return dt.timetuple()


class _FakeResponse:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content


class _FakeFeed:
    """Minimal feedparser-like object — only the fields news_rss reads."""

    def __init__(self, entries):
        self.entries = entries


class TestLocaleRouting(unittest.TestCase):
    def test_de_suffix_routes_german(self):
        self.assertEqual(
            news_rss._locale_for("RWE.DE")[:3], ("de", "DE", "DE:de"),
        )

    def test_us_default_for_bare_ticker(self):
        self.assertEqual(
            news_rss._locale_for("AAPL")[:3], ("en", "US", "US:en"),
        )

    def test_mi_routes_italian(self):
        self.assertEqual(
            news_rss._locale_for("3OIL.MI")[:3], ("it", "IT", "IT:it"),
        )


class TestQueryBuilder(unittest.TestCase):
    def test_query_adds_finance_keyword(self):
        q = news_rss._build_query("RWE.DE", "RWE AG", "aktie")
        self.assertIn("aktie", q)
        self.assertIn("RWE", q)

    def test_query_includes_company_name_when_distinct(self):
        q = news_rss._build_query("BAS.DE", "BASF SE", "aktie")
        self.assertIn("BASF SE", q)

    def test_query_skips_name_when_equal_to_bare(self):
        q = news_rss._build_query("AAPL", "AAPL", "stock")
        self.assertEqual(q, "AAPL stock")


class TestFetchRssNews(unittest.TestCase):
    def test_http_error_returns_empty(self):
        with patch("core.data.news_rss.requests.get", return_value=_FakeResponse(500)):
            self.assertEqual(news_rss.fetch_rss_news("AAPL"), [])

    def test_request_exception_returns_empty(self):
        import requests
        with patch(
            "core.data.news_rss.requests.get",
            side_effect=requests.ConnectionError("boom"),
        ):
            self.assertEqual(news_rss.fetch_rss_news("AAPL"), [])

    def test_recent_items_returned(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        entries = [
            {
                "title": "Fresh news",
                "published_parsed": _struct_for(recent),
                "source": {"title": "Reuters"},
            },
        ]
        with patch(
            "core.data.news_rss.requests.get",
            return_value=_FakeResponse(200, b"<rss/>"),
        ), patch(
            "core.data.news_rss.feedparser.parse",
            return_value=_FakeFeed(entries),
        ):
            out = news_rss.fetch_rss_news("AAPL", max_age_hours=24)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Fresh news")
        self.assertEqual(out[0]["source"], "Reuters")

    def test_old_items_filtered_out(self):
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        entries = [{
            "title": "Old news",
            "published_parsed": _struct_for(old),
            "source": {"title": "X"},
        }]
        with patch(
            "core.data.news_rss.requests.get",
            return_value=_FakeResponse(200, b"<rss/>"),
        ), patch(
            "core.data.news_rss.feedparser.parse",
            return_value=_FakeFeed(entries),
        ):
            out = news_rss.fetch_rss_news("AAPL", max_age_hours=24)
        self.assertEqual(out, [])


class TestFetchMacroRss(unittest.TestCase):
    def test_aggregates_across_locales_and_topics(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=1)

        def fake_get(url, **kwargs):
            # Each locale × topic call returns one unique entry. We assert
            # the function visits all _MACRO_LOCALES × _MACRO_TOPICS.
            return _FakeResponse(200, b"x")

        # feedparser is invoked once per HTTP call — return a single-item
        # feed each time, tagged via mutable counter so we can detect that
        # every (locale, topic) pair was hit.
        call_idx = {"i": 0}
        def fake_parse(content):
            i = call_idx["i"]
            call_idx["i"] = i + 1
            return _FakeFeed([{
                "title": f"Headline #{i}",
                "published_parsed": _struct_for(recent),
                "source": {"title": "FakeWire"},
            }])

        with patch("core.data.news_rss.requests.get", side_effect=fake_get), \
             patch("core.data.news_rss.feedparser.parse", side_effect=fake_parse):
            out = news_rss.fetch_macro_rss(max_age_hours=12, limit_per_feed=5)

        # 2 locales × 2 topics = 4 calls, each yields 1 item → 4 items
        self.assertEqual(len(out), 4)
        # Each item should carry its locale + topic
        topics = {item["topic"] for item in out}
        locales = {item["locale"] for item in out}
        self.assertEqual(topics, {"business", "world"})
        self.assertEqual(locales, {"DE", "US"})

    def test_http_failure_one_feed_does_not_abort_others(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=1)

        responses = iter([
            _FakeResponse(500),  # locale 1 / topic 1 → fails
            _FakeResponse(200, b"x"),
            _FakeResponse(200, b"x"),
            _FakeResponse(200, b"x"),
        ])
        def fake_get(url, **kwargs):
            return next(responses)

        def fake_parse(content):
            return _FakeFeed([{
                "title": "Good headline",
                "published_parsed": _struct_for(recent),
                "source": {"title": "Wire"},
            }])

        with patch("core.data.news_rss.requests.get", side_effect=fake_get), \
             patch("core.data.news_rss.feedparser.parse", side_effect=fake_parse):
            out = news_rss.fetch_macro_rss(max_age_hours=12)

        # 3 successes, 1 failure
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
