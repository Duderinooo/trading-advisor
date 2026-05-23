"""RSS news fetch via Google News.

Why: yfinance.news is US/EN-biased and lags German sources (Handelsblatt, WELT,
boerse.de) by 5-30min on DAX scandals. Google News RSS aggregates them all,
multilingual, no API key. Single feed pattern → no per-source mapping.

Two scan modes:
- fetch_rss_news(ticker)   — per-ticker, used by check_news_events main loop
- fetch_macro_rss(locales) — ticker-agnostic top-business headlines, used to
  catch geo/macro news (OPEC, ECB, war, sanctions) that no watchlist ticker
  would surface on its own
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import requests

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_TPL = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"
_TIMEOUT_SECONDS = 6

# Exchange suffix → (hl, gl, ceid, finance-keyword) for Google News locale routing.
_LOCALE_BY_SUFFIX = {
    "DE": ("de", "DE", "DE:de", "aktie"),
    "F": ("de", "DE", "DE:de", "aktie"),
    "PA": ("fr", "FR", "FR:fr", "action"),
    "AS": ("nl", "NL", "NL:nl", "aandeel"),
    "MI": ("it", "IT", "IT:it", "azione"),
    "MC": ("es", "ES", "ES:es", "acción"),
    "L": ("en", "GB", "GB:en", "stock"),
    "TO": ("en", "CA", "CA:en", "stock"),
    "HK": ("en", "HK", "HK:en", "stock"),
    "T": ("ja", "JP", "JP:ja", "株"),
}
_DEFAULT_LOCALE = ("en", "US", "US:en", "stock")


def _clean_name(name: str | None) -> str | None:
    """yfinance shortName sometimes has trailing whitespace + garbage chars
    (e.g. 'RWE AG                        I'). Take everything up to the first
    run of 2+ spaces, strip, collapse internal whitespace. Returns None if empty.
    """
    if not name:
        return None
    head = re.split(r"\s{2,}", name, maxsplit=1)[0]
    cleaned = re.sub(r"\s+", " ", head).strip()
    return cleaned or None


def _locale_for(ticker: str) -> tuple[str, str, str, str]:
    """Return (hl, gl, ceid, finance_keyword) based on exchange suffix."""
    if "." not in ticker:
        return _DEFAULT_LOCALE
    suffix = ticker.split(".")[-1].upper()
    return _LOCALE_BY_SUFFIX.get(suffix, _DEFAULT_LOCALE)


def _build_query(ticker: str, name: str | None, finance_kw: str) -> str:
    """Compose a Google News query that disambiguates the ticker symbol.

    Bare 3-letter codes ('RWE', 'VW') collide with non-financial content
    (football clubs, abbreviations). Adding a locale-appropriate finance
    keyword ('aktie' / 'stock' / 'action') filters to finance-domain hits.
    """
    bare = ticker.split(".")[0]
    cleaned = _clean_name(name)
    if cleaned and cleaned.lower() != bare.lower():
        return f'({bare} OR "{cleaned}") {finance_kw}'
    return f"{bare} {finance_kw}"


def fetch_rss_news(
    ticker: str,
    name: str | None = None,
    max_age_hours: int = 24,
    limit: int = 25,
) -> list[dict]:
    """Pull recent news for ticker (or company name) via Google News RSS.

    Returns list of {'title', 'source', 'published_at'} newest-first within max_age_hours.
    Empty list on any failure (network, parse) — never raises.
    """
    hl, gl, ceid, finance_kw = _locale_for(ticker)
    query = _build_query(ticker, name, finance_kw)
    url = _GOOGLE_NEWS_TPL.format(q=quote_plus(query), hl=hl, gl=gl, ceid=ceid)

    # NOTE: feedparser.parse(url) opens its own socket and ignores socket.setdefaulttimeout
    # except by mutating it process-globally — racy with the Telegram listener thread.
    # Fetch via requests with a local timeout, then hand the bytes to feedparser.
    try:
        r = requests.get(url, timeout=_TIMEOUT_SECONDS, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        feed = feedparser.parse(r.content)
    except (requests.RequestException, Exception) as e:
        logger.warning("RSS fetch failed for %r: %s", query, e)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items = []
    for entry in feed.entries[:limit]:
        title = entry.get("title", "")
        if not title:
            continue
        pub_struct = entry.get("published_parsed")
        pub_dt = None
        if pub_struct:
            pub_dt = datetime(*pub_struct[:6], tzinfo=timezone.utc)
            if pub_dt < cutoff:
                continue
        items.append({
            "title": title,
            "source": (entry.get("source") or {}).get("title", "?"),
            "published_at": pub_dt.isoformat() if pub_dt else None,
        })
    return items


# Google News topical-feed IDs:
#   b   = Business
#   w   = World
#   tc  = Technology
#   nat = Nation
# We pull Business + World — Business has stock-relevant headlines, World
# catches geo events (war, sanctions, OPEC, central-bank decisions).
_MACRO_TOPICS = ("BUSINESS", "WORLD")

# Locales to pull. DE + US cover ~all our watchlist (XETRA + US-listed
# commodities). Adding more locales scales the request count linearly,
# so keep this short. ceid encodes country:lang.
_MACRO_LOCALES = (
    ("de", "DE", "DE:de"),
    ("en", "US", "US:en"),
)

_GOOGLE_TOPIC_TPL = (
    "https://news.google.com/rss/headlines/section/topic/{topic}?hl={hl}&gl={gl}&ceid={ceid}"
)


def fetch_macro_rss(
    max_age_hours: int = 6,
    limit_per_feed: int = 30,
) -> list[dict]:
    """Pull top business + world headlines from Google News topical feeds
    (DE + US locales). Returns list of {'title', 'source', 'published_at',
    'locale', 'topic'} newest-first within max_age_hours.

    max_age_hours intentionally shorter than the per-ticker fetch (24h)
    because macro/geo signals decay fast — yesterday's OPEC headline
    isn't actionable today, but yesterday's earnings-beat thesis on a
    held position still is.

    Empty list on any failure — never raises.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: list[dict] = []
    for hl, gl, ceid in _MACRO_LOCALES:
        for topic in _MACRO_TOPICS:
            url = _GOOGLE_TOPIC_TPL.format(topic=topic, hl=hl, gl=gl, ceid=ceid)
            try:
                r = requests.get(
                    url,
                    timeout=_TIMEOUT_SECONDS,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if r.status_code != 200:
                    continue
                feed = feedparser.parse(r.content)
            except (requests.RequestException, Exception) as e:
                logger.warning(
                    "Macro RSS fetch failed for topic=%s locale=%s: %s",
                    topic, ceid, e,
                )
                continue

            for entry in feed.entries[:limit_per_feed]:
                title = entry.get("title", "")
                if not title:
                    continue
                pub_struct = entry.get("published_parsed")
                pub_dt = None
                if pub_struct:
                    pub_dt = datetime(*pub_struct[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                items.append({
                    "title": title,
                    "source": (entry.get("source") or {}).get("title", "?"),
                    "published_at": pub_dt.isoformat() if pub_dt else None,
                    "locale": gl,
                    "topic": topic.lower(),
                })
    return items
