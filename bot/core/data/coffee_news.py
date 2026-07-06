"""Coffee / Brazil-weather news tracker.

User holds a WisdomTree coffee ETC (long the coffee price). Brazil is the swing
producer, so the price is driven by its weather + harvest. This module surfaces
coffee-price-relevant headlines and classifies them by PRICE DIRECTION —
deterministically by keyword, no LLM (classification is a rule, not an
interpretation; also dodges the Haiku divergence problem).

Two signals, from the long holder's view:
  🟢 PREIS_HOCH   — supply shock → price up → GOOD for the position
                    (frost/geada, drought/seca, El Niño, heatwave, storms,
                     crop damage, shortage, price rally)
  🔴 PREIS_RUNTER — oversupply → price down → BAD for the position
                    (record/bumper harvest, surplus, favourable weather,
                     higher output, price slump)

Pure info channel — NOT an auto-trade signal (the ETC isn't on the watchlist /
gate pipeline). Same shape + dedup contract as the Berkshire tracker.
"""

import logging
import re

from core.data.news_rss import fetch_rss_for_query

logger = logging.getLogger(__name__)

# Google News queries. NOTE: Google News RSS chokes on boolean OR / quoted
# phrases (returns 0) — verified 2026-06-10. Plain multi-word queries only.
# Cover Brazil weather + harvest + price, EN + DE + PT (geada = frost).
_QUERIES = [
    ("Brazil coffee frost", "en", "US", "US:en"),
    ("Brazil coffee harvest", "en", "US", "US:en"),
    ("coffee price weather", "en", "US", "US:en"),
    ("arabica coffee Brazil", "en", "US", "US:en"),
    ("Minas Gerais coffee rain", "en", "US", "US:en"),
    ("ICE coffee inventories", "en", "US", "US:en"),   # certified-stocks catalyst
    ("Conab USDA coffee crop", "en", "US", "US:en"),   # official damage reports
    ("Kaffee Preis Brasilien", "de", "DE", "DE:de"),
    ("cafe Brasil geada safra", "pt", "BR", "BR:pt-419"),
]

# Deterministic classification. Checked on title+summary, lowercased.
# PREIS_HOCH = supply threat / bullish price action.
_UP_KW = [
    # weather / supply shock
    "frost", "freeze", "freezing", "drought", "dry weather", "dryness",
    "el nino", "el niño", "la nina", "la niña", "heatwave", "heat wave",
    "storm", "flood", "excessive rain", "adverse weather", "crop damage",
    "crop loss", "harvest loss", "damaged crop", "shortage", "deficit",
    "supply cut", "output cut", "lower output", "reduced crop",
    "smaller crop", "supply concern", "tight supply",
    "frost warning", "cold front", "cold snap", "bud damage",
    "flowering damage", "crop concern",
    # inventory / positioning squeeze (bullish for price)
    "short squeeze", "squeeze", "multi-year low", "multi year low",
    "low inventories", "low stocks", "falling stocks", "dwindling stocks",
    "declining inventories", "supply squeeze", "strong real",
    # price action up
    "rally", "rallies", "surge", "surges", "jumps", "soars", "spike",
    "hits record", "record high", "climbs", "rises to", "price spike",
    # de
    "dürre", "unwetter", "ernteausfall", "knappheit", "hitzewelle",
    "verteuert", "steigt", "rekordhoch", "kälteeinbruch", "frostwarnung",
    "kältefront", "niedrige lagerbestände",
    # pt
    "geada", "seca", "quebra de safra", "clima adverso", "onda de frio",
]
# PREIS_RUNTER = oversupply / bearish price action.
_DOWN_KW = [
    # supply glut
    "record harvest", "record crop", "record output", "record production",
    "bumper crop", "bumper harvest", "surplus", "glut", "oversupply",
    "bigger crop", "larger crop", "higher output", "output rise",
    "output increase", "production rise", "abundant", "ample supply",
    "good weather", "favourable weather", "favorable weather",
    "favourable rain", "beneficial rain",
    # official-forecast / inventory / fx pressure (bearish for price)
    "usda record", "record season", "rabobank surplus", "global surplus",
    "rising stocks", "rising inventories", "inventories rise",
    "stocks rise", "weaker real", "real weakens", "strong exports",
    "export surge", "rising exports",
    # price action down
    "falls", "drops", "declines", "slumps", "tumbles", "price drop",
    "lower price", "eases", "retreats", "sinks",
    # de
    "rekordernte", "überangebot", "überschuss", "gute ernte", "fällt",
    "sinkt", "verbilligt", "günstige witterung", "steigende lagerbestände",
    # pt
    "safra recorde", "supersafra", "excedente", "boa safra",
]


# "record <n words> crop/harvest/output" = oversupply (bearish). Regex, not a
# substring, because headlines split it ("record Brazil coffee crop"). Distinct
# from "record high" (bullish price action, kept in _UP_KW).
_RECORD_CROP_RE = re.compile(
    r"record\b.{0,25}\b(crop|harvest|output|production|season|safra)", re.I,
)


def _classify(text: str) -> str:
    """Return PREIS_HOCH / PREIS_RUNTER / INFO from headline+summary."""
    t = text.lower()
    up = any(kw in t for kw in _UP_KW)
    down = any(kw in t for kw in _DOWN_KW) or bool(_RECORD_CROP_RE.search(t))
    if up and not down:
        return "PREIS_HOCH"
    if down and not up:
        return "PREIS_RUNTER"
    return "INFO"  # both (ambiguous) or neither (commentary)


def fetch_coffee_news(max_age_hours: int = 24) -> list[dict]:
    """Fetch + classify recent coffee/Brazil-weather headlines.

    Returns newest-first list of
    {classification, title, source, link, published_at, summary}.
    Dedup is the caller's job (headline can appear in several feeds).
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
