"""News-event detection: yfinance + Google News RSS scan + headline classification.

Precision > Recall — prefer false-negatives over false-positives. Word-boundary
regex match; trailing `*` = compound-stem (`kw\\w*`). Stems used only for domain-
specific German compounds where English-word collision is impossible.
"""

import hashlib
import logging
import re
from datetime import date, datetime, timedelta

import yfinance as yf

import config
from core.events.types import EventType
from core.data.market_data import get_market_data
from core.data.news_rss import fetch_rss_news
from core.portfolio import (
    portfolio_lock, load_portfolio, save_portfolio,
    exit_suppressed_tickers,
)
from core.portfolio.dedup_store import load_dedup, save_dedup


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyword patterns
# ---------------------------------------------------------------------------

def _kw_to_pattern_part(kw: str) -> str:
    """Regex fragment for one keyword. `kw*` → stem (\\bkw\\w*\\b); else exact (\\bkw\\b)."""
    if kw.endswith("*"):
        return r"\b" + re.escape(kw[:-1]) + r"\w*\b"
    return r"\b" + re.escape(kw) + r"\b"


def _build_keyword_pattern(keywords: list[str]) -> re.Pattern:
    return re.compile(
        "(?:" + "|".join(_kw_to_pattern_part(kw) for kw in keywords) + ")",
        re.IGNORECASE,
    )


_STOCK_NEWS_KEYWORDS = [
    # English — earnings/results (bigrams for ambiguous "beat"/"miss"/"cut")
    "earnings", "earnings beat", "earnings miss",
    "revenue beat", "revenue miss",
    "guidance", "raised guidance", "lowered guidance", "cut guidance",
    "raised forecast", "lowered forecast", "cut forecast",
    "profit warning",
    # English — analyst actions
    "upgrade", "upgrades", "upgraded",
    "downgrade", "downgrades", "downgraded",
    "outperform", "outperforms", "outperformed",
    "underperform", "underperforms", "underperformed",
    "raised target", "raises target", "lowered target", "lowers target",
    "price target", "cut target", "cuts target",
    "initiated coverage", "initiates coverage",
    "buy rating", "sell rating", "hold rating",
    "overweight", "underweight",
    # English — M&A
    "acquisition", "acquisitions",
    "merger", "mergers",
    "takeover", "takeovers",
    "buyout", "buyouts",
    # English — regulatory
    "fda approval", "fda rejection", "fda warning",
    "drug approval", "drug rejection",
    "recall", "recalls", "recalled",
    # English — leadership/legal
    "ceo", "cfo",
    "resign", "resigns", "resigned", "resignation",
    "investigation", "investigations", "investigated",
    "lawsuit", "lawsuits",
    "fraud", "fraudulent",
    # English — restructuring
    "layoff", "layoffs",
    "restructuring", "restructured", "restructure",
    "bankruptcy", "bankrupt",
    "loan default", "debt default", "default risk",
    # English — capital actions
    "dividend", "dividends",
    "buyback", "buybacks",
    "stock split", "share split", "reverse split",
    # German — compound stems (precision-safe, domain-specific)
    "übernahm*",
    "quartalszahl*", "quartalsergebnis*",
    "gewinnwarn*",
    "gewinneinbruch*", "gewinnsprung*",
    "prognose*",
    "insolvenz*",
    "stellenabbau*",
    "rücktritt*",
    "kursziel*",
    "hochstuf*", "hochgestuft",
    "herabstuf*", "herabgestuft",
    "abstuf*",
    "skandal*",
    "ermittlung*",
    "betrugs*",
    "dividend*",
    "aktienrückkauf*",
    "umsatzsprung*", "umsatzeinbruch*",
    # German — exact + bigrams for analyst recs
    "fusion", "fusionen",
    "ausblick",
    "kaufempfehl*", "verkaufsempfehl*", "halteempfehl*",
    "auf kaufen", "auf verkaufen", "auf halten",
]

# Subset that signals fresh analyst action — bumps priority to HIGH + tags
# subtype=rating_change so Claude weights above stale consensus.
_RATING_CHANGE_KEYWORDS = [
    "upgrade", "upgrades", "upgraded",
    "downgrade", "downgrades", "downgraded",
    "outperform", "outperforms", "outperformed",
    "underperform", "underperforms", "underperformed",
    "raised target", "raises target", "lowered target", "lowers target",
    "price target", "cut target", "cuts target",
    "initiated coverage", "initiates coverage",
    "buy rating", "sell rating", "hold rating",
    "overweight", "underweight",
    "kursziel*",
    "hochstuf*", "hochgestuft",
    "herabstuf*", "herabgestuft",
    "abstuf*",
    "kaufempfehl*", "verkaufsempfehl*", "halteempfehl*",
    "auf kaufen", "auf verkaufen", "auf halten",
]

_STOCK_NEWS_PATTERN = _build_keyword_pattern(_STOCK_NEWS_KEYWORDS)
_RATING_CHANGE_PATTERN = _build_keyword_pattern(_RATING_CHANGE_KEYWORDS)
_COMMODITY_TRIGGER_PATTERNS = {
    comm: _build_keyword_pattern(kws)
    for comm, kws in config.COMMODITY_TRIGGERS.items()
}


# ---------------------------------------------------------------------------
# Headline classification
# ---------------------------------------------------------------------------

def _ticker_in_title(title: str, ticker: str, name: str | None) -> bool:
    """Plausible-mention check: bare ticker symbol OR ≥4-char company-name token.
    Filters tangential RSS hits. Does not disambiguate symbol-collision edge cases
    (e.g. ticker RWE vs RWE Essen football); upstream keyword query is primary defense."""
    title_lower = title.lower()
    bare = ticker.split(".")[0].lower()
    if re.search(r"\b" + re.escape(bare) + r"\b", title_lower):
        return True
    if name:
        name_lower = name.lower()
        if name_lower in title_lower:
            return True
        # Match on ANY ≥4-char token (premature break on first token had false-
        # positives on "international" in "International Business Machines").
        for tok in re.findall(r"\w+", name_lower):
            if len(tok) >= 4 and re.search(r"\b" + re.escape(tok) + r"\b", title_lower):
                return True
    return False


def _classify_headline(
    title: str,
    ticker: str,
    is_open_position: bool,
    name: str | None = None,
) -> dict | None:
    """Match headline against geo/stock/rating keywords. Returns event dict or None."""
    triggered = [
        comm for comm, pat in _COMMODITY_TRIGGER_PATTERNS.items()
        if pat.search(title)
    ]
    if triggered:
        return {
            "type": EventType.NEWS_GEO,
            "headline": title,
            "triggered_commodities": triggered,
            "source_ticker": ticker,
            "priority": "HIGH",
        }

    if ticker in config.MARKET_INDICATORS or ticker in config.COMMODITIES:
        return None

    # Ticker-relevance gate: drop headlines that don't mention the ticker/company.
    if not _ticker_in_title(title, ticker, name):
        return None

    is_rating_change = bool(_RATING_CHANGE_PATTERN.search(title))
    has_stock_kw = bool(_STOCK_NEWS_PATTERN.search(title))
    if not (is_rating_change or has_stock_kw):
        return None

    priority = "HIGH" if is_open_position else "MEDIUM"
    subtype = "rating_change" if is_rating_change else "stock_news"
    return {
        "type": EventType.NEWS_STOCK,
        "subtype": subtype,
        "headline": title,
        "source_ticker": ticker,
        "priority": priority,
    }


# ---------------------------------------------------------------------------
# Public scan entry point
# ---------------------------------------------------------------------------

def check_news_events() -> list[dict]:
    """Scan yfinance + Google News RSS for all watchlist/open/commodity tickers.
    Returns new actionable events not seen before today.
    Persists seen article hashes in portfolio.json (under lock)."""
    with portfolio_lock:
        portfolio = load_portfolio()
        today = str(date.today())
        dedup = load_dedup()
        seen_today = set(dedup.get("seen_news", {}).get(today, []))

        # Exit-suppressed: skip news scan for tickers with pending Exit / cooldown.
        # Saves Haiku calls on headlines user would ignore anyway.
        suppressed = {t.upper() for t in exit_suppressed_tickers(portfolio)}
        open_tickers = {
            t["ticker"] for t in portfolio.get("open_trades", [])
            if (t.get("ticker") or "").upper() not in suppressed
        }
        scan_tickers = [
            t for t in dict.fromkeys(
                list(config.MARKET_INDICATORS) + list(open_tickers)
                + config.WATCHLIST + config.COMMODITIES
            )
            if t.upper() not in suppressed
        ]

        # RSS scan only for stock tickers (open + watchlist). Skip indices/commodities —
        # yfinance covers those, and RSS query "SPY" or "GC=F" returns junk.
        rss_tickers = (set(open_tickers) | set(config.WATCHLIST)) - suppressed

        # Pull company names from market_data cache to disambiguate ticker queries
        # (e.g. "RWE" alone matches Rot-Weiss Essen football headlines).
        names_by_ticker = {}
        if rss_tickers:
            try:
                snapshots = get_market_data(list(rss_tickers))
                names_by_ticker = {
                    t: (s or {}).get("name") for t, s in snapshots.items()
                }
            except Exception as e:
                logger.warning("market_data lookup for RSS names failed: %s", e)

        events = []
        new_hashes = []

        for ticker in scan_tickers:
            is_open = ticker in open_tickers
            titles: list[str] = []

            # Source 1: yfinance (US/EN-biased, fast)
            try:
                items = yf.Ticker(ticker).news or []
                titles.extend(it.get("title", "") for it in items)
            except Exception:
                pass

            # Source 2: Google News RSS (multilingual, catches German sources)
            if ticker in rss_tickers:
                try:
                    rss_items = fetch_rss_news(
                        ticker,
                        name=names_by_ticker.get(ticker),
                        max_age_hours=24,
                    )
                    titles.extend(it["title"] for it in rss_items)
                except Exception as e:
                    logger.warning("RSS fetch failed for %s: %s", ticker, e)

            for title in titles:
                if not title:
                    continue
                h = hashlib.md5(title.lower().encode()).hexdigest()[:16]
                if h in seen_today:
                    continue
                new_hashes.append(h)
                seen_today.add(h)  # within-cycle dedup (same story across sources)

                event = _classify_headline(
                    title, ticker,
                    is_open_position=is_open,
                    name=names_by_ticker.get(ticker),
                )
                if event:
                    events.append(event)

        if new_hashes:
            # 2026-05-02: see research/2026-05-02-news-dedup-midnight.md
            # Keep yesterday alongside today so midnight rollover doesn't
            # re-fire 24h-old RSS articles.
            yesterday = str(date.today() - timedelta(days=1))
            existing = dedup.get("seen_news") or {}
            dedup["seen_news"] = {
                today: list(seen_today),
                yesterday: list(existing.get(yesterday, [])),
            }
            save_dedup(dedup)

        return events
