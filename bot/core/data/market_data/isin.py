"""ISIN resolution + cache for yfinance tickers."""

"""Market data fetching: yfinance snapshots, technical indicators, per-ticker
cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, datetime, timedelta

import yfinance as yf

import config


logger = logging.getLogger(__name__)

_isin_cache: dict[str, str | None] = {}

def _isin_for_ticker(ticker: str, stock=None) -> str | None:
    """Resolve ISIN. Order: hardcoded map (livefeed.TICKER_ISIN_MAP, source of
    truth for .DE Tradegate symbols) → yfinance Ticker.isin (works for US
    tickers, returns '-' for .DE). Result cached process-local. Returns None
    for indices/commodities without a clean ISIN."""
    if ticker in _isin_cache:
        return _isin_cache[ticker]
    # Hardcoded first: yfinance returns '-' for every .DE symbol.
    try:
        from core.data.livefeed import TICKER_ISIN_MAP
        if ticker in TICKER_ISIN_MAP:
            isin = TICKER_ISIN_MAP[ticker]
            _isin_cache[ticker] = isin
            return isin
    except Exception:
        pass
    try:
        st = stock or yf.Ticker(ticker)
        isin = getattr(st, "isin", None)
        if not isin or not isinstance(isin, str) or len(isin) != 12 or isin == "-":
            _isin_cache[ticker] = None
            return None
        _isin_cache[ticker] = isin
        return isin
    except Exception:
        _isin_cache[ticker] = None
        return None


# ---------- Indicators ----------
