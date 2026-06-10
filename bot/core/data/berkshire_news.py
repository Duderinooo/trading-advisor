"""Berkshire / Sogo-Shosha news tracker.

User holds the Buffett thesis on the five Japanese trading houses (sogo shosha:
Mitsubishi 8058, Mitsui 8031, Sumitomo 8053, Itochu 8001, Marubeni 8002). This
module surfaces Berkshire-related headlines about those stakes and classifies
them buy / hold / sell — deterministically by keyword, no LLM (classification
is a rule, not an interpretation; also dodges the Haiku divergence problem).

Pure info channel — NOT a trade signal. The houses aren't on TR / the watchlist.
The user just wants a ping + link when Berkshire moves on the thesis, plus the
yearly AGM / annual-letter coverage that flows through the same feed.
"""

import logging
import re

from core.data.news_rss import fetch_rss_for_query

logger = logging.getLogger(__name__)

# Google News queries. NOTE: Google News RSS chokes on boolean OR / quoted
# phrases (returns 0) — verified 2026-06-10. Use plain multi-word queries only.
# These five cover stake-change news (5%-threshold filings), the sogo-shosha
# collective, and the yearly AGM / annual-letter coverage, EN + DE.
_QUERIES = [
    ("Berkshire Japan stake", "en", "US", "US:en"),
    ("Berkshire Japan trading houses", "en", "US", "US:en"),
    ("Berkshire annual meeting Japan", "en", "US", "US:en"),
    ("Berkshire Japan", "de", "DE", "DE:de"),
    ("Buffett Japan Investment", "de", "DE", "DE:de"),
]

# Deterministic classification. Checked on title+summary, lowercased.
# `*` suffix = compound-stem (German loves them: "aufgestockt", "Beteiligung").
_BUY_KW = [
    "raise", "raised", "raises", "increase", "increased", "increases",
    "boost", "boosted", "add", "added", "ups stake", " raises stake",
    "lifts", "lifted", "accumulat", "aufgestockt", "erhöht", "erhöhung",
    "ausgebaut", "aufgestockt", "stockt", "aufstockung", "beteiligung erhöht",
]
_SELL_KW = [
    "sold", "sell", "sells", "cut", "cuts", "trim", "trimmed", "trims",
    "reduce", "reduced", "reduces", "slash", "pares", "pared", "exit",
    "verkauft", "verkauf", "reduziert", "abgebaut", "ausstieg", "trennt sich",
]
_HOLD_KW = [
    "maintain", "maintains", "holds", "unchanged", "keeps", "stays",
    "hält", "behält", "unverändert", "festhalten", "bleibt investiert",
]


def _classify(text: str) -> str:
    """Return AUFSTOCKUNG / VERKAUF / HALTEN / INFO from headline+summary."""
    t = text.lower()
    buy = any(kw in t for kw in _BUY_KW)
    sell = any(kw in t for kw in _SELL_KW)
    if buy and not sell:
        return "AUFSTOCKUNG"
    if sell and not buy:
        return "VERKAUF"
    if any(kw in t for kw in _HOLD_KW) and not (buy or sell):
        return "HALTEN"
    return "INFO"  # AGM, annual letter, commentary, or ambiguous


def fetch_berkshire_news(max_age_hours: int = 24) -> list[dict]:
    """Fetch + classify recent Berkshire/Sogo-Shosha headlines.

    Returns newest-first list of
    {classification, title, source, link, published_at, summary}.
    Dedup is the caller's job (headline can appear in both EN + DE feeds).
    """
    seen_titles: set[str] = set()
    out: list[dict] = []
    for query, hl, gl, ceid in _QUERIES:
        items = fetch_rss_for_query(
            query, hl=hl, gl=gl, ceid=ceid, max_age_hours=max_age_hours,
        )
        for it in items:
            title = it.get("title", "")
            key = re.sub(r"\s+", " ", title.lower()).strip()
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            it["classification"] = _classify(f"{title} {it.get('summary', '')}")
            out.append(it)
    return out
