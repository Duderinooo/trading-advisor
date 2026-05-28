"""BaFin Director's Dealings — scraped from tracefour.com/de/_payload.json.

Nuxt 3 SSG payload: flat cross-reference array. Resolve by scanning for a list
whose first element hydrates to a dict with both `filingId` and `isin` keys.
Cache TTL matches Cache-Control: max-age=3600 on the endpoint.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

_PAYLOAD_URL = "https://tracefour.com/de/_payload.json"
_CACHE_TTL = 3600
_LOOKBACK_DAYS = 30
_REQUEST_TIMEOUT = 15

_cache: dict = {"ts": 0.0, "filings": []}


def _resolve(raw: list, val, depth: int = 0):
    """Recursively resolve Nuxt cross-reference indices. Primitives pass through."""
    if depth > 15 or not isinstance(val, int) or val < 0 or val >= len(raw):
        return val
    item = raw[val]
    if isinstance(item, dict):
        return {k: _resolve(raw, v, depth + 1) for k, v in item.items()}
    if isinstance(item, list):
        return [_resolve(raw, v, depth + 1) for v in item]
    return item


def _find_filings(raw: list) -> list[dict]:
    """Scan payload for the list-of-filings array without relying on fixed indices.

    Filings array: each element is an int index pointing to a dict that has
    both `filingId` and `isin` string fields.
    """
    for item in raw:
        if not isinstance(item, list) or len(item) < 5:
            continue
        first_ref = item[0]
        if not isinstance(first_ref, int) or first_ref >= len(raw):
            continue
        first = raw[first_ref]
        if not isinstance(first, dict):
            continue
        # Both keys must be present as int indices pointing to strings
        fid = first.get("filingId")
        isin = first.get("isin")
        if not isinstance(fid, int) or not isinstance(isin, int):
            continue
        if not isinstance(raw[fid] if fid < len(raw) else None, str):
            continue
        if not isinstance(raw[isin] if isin < len(raw) else None, str):
            continue
        return [_resolve(raw, idx) for idx in item if isinstance(idx, int)]
    return []


def _fetch_raw() -> list[dict]:
    try:
        r = requests.get(
            _PAYLOAD_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; trading-advisor/1.0)"},
            timeout=_REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        raw = r.json()
        if not isinstance(raw, list):
            logger.warning("insider_data: unexpected payload type %s", type(raw))
            return []
        filings = _find_filings(raw)
        logger.info("insider_data: fetched %d filings from tracefour", len(filings))
        return filings
    except Exception as exc:
        logger.warning("insider_data: fetch failed: %s", exc)
        return []


def get_filings(lookback_days: int = _LOOKBACK_DAYS) -> list[dict]:
    """Return recent BaFin director dealings, 1h cached."""
    now = time.monotonic()
    if now - _cache["ts"] < _CACHE_TTL and _cache["filings"]:
        return _cache["filings"]
    filings = _fetch_raw()
    _cache.update({"ts": now, "filings": filings})
    return filings


def insider_signals_for_tickers(tickers: list[str]) -> dict[str, dict]:
    """Summarise BaFin director dealings for the given tickers over last 30 days.

    Uses TICKER_ISIN_MAP to reverse-map ISIN → ticker symbol.

    Returns per ticker:
        buys            — count of buy filings
        sells           — count of sell filings
        buy_value_eur   — total buy volume EUR
        sell_value_eur  — total sell volume EUR
        cluster_buy     — True if 3+ distinct insiders bought
        latest_buy      — {date, value_eur, insider, capacity} or None
        latest_sell     — same or None
    """
    try:
        from core.data.livefeed import TICKER_ISIN_MAP
    except Exception as exc:
        logger.warning("insider_data: cannot import TICKER_ISIN_MAP: %s", exc)
        return {}

    isin_to_ticker = {isin: t for t, isin in TICKER_ISIN_MAP.items() if t in tickers}
    if not isin_to_ticker:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
    filings = get_filings()

    by_ticker: dict[str, list[dict]] = {}
    for f in filings:
        isin = f.get("isin", "")
        ticker = isin_to_ticker.get(isin)
        if not ticker:
            continue
        pub_str = f.get("publicationAt", "")
        try:
            pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if pub < cutoff:
            continue
        by_ticker.setdefault(ticker, []).append(f)

    result: dict[str, dict] = {}
    for ticker, fs in by_ticker.items():
        buys = [f for f in fs if f.get("transactionNature") == "Buy"]
        sells = [f for f in fs if f.get("transactionNature") == "Sell"]

        def _latest(lst: list[dict]) -> dict | None:
            if not lst:
                return None
            f = max(lst, key=lambda x: x.get("transactionDate", ""))
            return {
                "date": f.get("transactionDate"),
                "value_eur": round(f.get("totalValue", 0)),
                "insider": f.get("pdmrName", ""),
                "capacity": f.get("capacity", ""),
            }

        result[ticker] = {
            "buys": len(buys),
            "sells": len(sells),
            "buy_value_eur": round(sum(f.get("totalValue", 0) for f in buys)),
            "sell_value_eur": round(sum(f.get("totalValue", 0) for f in sells)),
            "cluster_buy": len({f.get("pdmrName") for f in buys}) >= 3,
            "latest_buy": _latest(buys),
            "latest_sell": _latest(sells),
        }

    return result
