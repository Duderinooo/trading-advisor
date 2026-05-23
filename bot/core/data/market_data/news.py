"""yfinance news fetch (US-biased fast path; RSS supplements via core/data/news_rss)."""

"""Market data fetching: yfinance snapshots, technical indicators, per-ticker
cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, datetime, timedelta

import yfinance as yf

import config


logger = logging.getLogger(__name__)

def fetch_news(tickers: list[str], limit_per_ticker: int = 3) -> dict[str, list[str]]:
    """Fetch recent headlines per ticker for the analyzer prompt context.

    Merges yfinance.news (US/EN-biased) with Google News RSS (multilingual,
    catches Handelsblatt/boerse.de/AD HOC for `.DE` tickers). Pre-fix yfinance-
    only path left Sonnet blind to German DAX news in its morning prompt
    (Bug 2026-05-02 audit). Newest-first dedup by lowercased title.
    """
    from core.data.news_rss import fetch_rss_news

    # Resolve company name once per ticker (RSS query disambiguator).
    name_lookup: dict[str, str | None] = {}
    for ticker in tickers:
        try:
            name_lookup[ticker] = yf.Ticker(ticker).info.get("shortName")
        except Exception:
            name_lookup[ticker] = None

    result: dict[str, list[str]] = {}
    for ticker in tickers:
        merged: list[str] = []
        seen: set[str] = set()

        try:
            yf_items = yf.Ticker(ticker).news or []
            for it in yf_items:
                t = (it.get("title") or "").strip()
                if t and t.lower() not in seen:
                    merged.append(t)
                    seen.add(t.lower())
        except Exception:
            pass

        try:
            rss_items = fetch_rss_news(ticker, name=name_lookup.get(ticker), max_age_hours=24)
            for it in rss_items:
                t = (it.get("title") or "").strip()
                if t and t.lower() not in seen:
                    merged.append(t)
                    seen.add(t.lower())
        except Exception:
            pass

        if merged:
            result[ticker] = merged[:limit_per_ticker]
    return result


# ---------- Market regime ----------
