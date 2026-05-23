"""Public get_market_data wrapper + 60s in-memory cache + invalidation."""

"""Market data fetching: yfinance snapshots, technical indicators, per-ticker
cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, datetime, timedelta

import yfinance as yf

import config


logger = logging.getLogger(__name__)

from core.data.market_data.fetch import _annotate_relative_strength, _fetch_ticker

_market_cache: dict[str, tuple[dict, float]] = {}

def get_market_data(tickers: list[str], ttl_seconds: int = None) -> dict:
    """Fetch current market data, using per-ticker cache. Cache hits skip yfinance entirely."""
    if ttl_seconds is None:
        ttl_seconds = config.MARKET_DATA_CACHE_TTL_SECONDS

    now = _time.time()
    data = {}

    for ticker in tickers:
        cached = _market_cache.get(ticker)
        if cached and (now - cached[1]) < ttl_seconds:
            data[ticker] = cached[0]
            continue

        try:
            fresh = _fetch_ticker(ticker)
            _market_cache[ticker] = (fresh, now)
            data[ticker] = fresh
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", ticker, e)
            data[ticker] = {"error": str(e)}

    # Post-process: relative strength vs index (20d perf delta).
    # Index ticker fetched on-demand if not in batch.
    _annotate_relative_strength(data)
    return data

def invalidate_market_cache():
    """Force next get_market_data call to re-fetch from yfinance."""
    _market_cache.clear()


# ---------- Period return (for trade attribution: alpha vs market beta) ----------
