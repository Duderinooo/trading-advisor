"""Earnings calendar fetch + warning queries (T-N to T+0 hard-block)."""

"""Market data fetching: yfinance snapshots, technical indicators, per-ticker
cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, datetime, timedelta

import yfinance as yf

import config


logger = logging.getLogger(__name__)

_earnings_cache: dict[str, tuple[list, float]] = {}
_EARNINGS_CACHE_TTL = 6 * 3600

def _fetch_earnings(ticker: str) -> list[dict]:
    """Fetch nearest upcoming earnings date for a ticker via yfinance."""
    results = []
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return results
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
            if not isinstance(dates, list):
                dates = [dates]
        else:
            return results
        today = date.today()
        for ed in dates:
            if hasattr(ed, "date"):
                ed = ed.date()
            elif isinstance(ed, str):
                try:
                    ed = date.fromisoformat(ed[:10])
                except ValueError:
                    continue
            if isinstance(ed, date) and ed >= today:
                results.append({"ticker": ticker, "earnings_date": str(ed), "days_until": (ed - today).days})
                break
    except Exception as e:
        logger.debug("Earnings fetch failed for %s: %s", ticker, e)
    return results


# ---------- Dividends ----------

def get_earnings_warnings(tickers: list[str], days_ahead: int = 3) -> list[dict]:
    """Return tickers with earnings within days_ahead days. Cached 6h per ticker."""
    now = _time.time()
    cutoff = date.today() + timedelta(days=days_ahead + 2)
    warnings = []
    for ticker in tickers:
        cached = _earnings_cache.get(ticker)
        if cached and (now - cached[1]) < _EARNINGS_CACHE_TTL:
            data = cached[0]
        else:
            data = _fetch_earnings(ticker)
            _earnings_cache[ticker] = (data, now)
        for w in data:
            if date.fromisoformat(w["earnings_date"]) <= cutoff:
                warnings.append(w)
    return sorted(warnings, key=lambda w: w["earnings_date"])


# ---------- News headlines (for prompt context) ----------
