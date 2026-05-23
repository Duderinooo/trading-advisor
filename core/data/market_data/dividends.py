"""Ex-dividend calendar + SL-threat warnings (mechanical drop ≥ 50% SL distance)."""

"""Market data fetching: yfinance snapshots, technical indicators, per-ticker
cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, datetime, timedelta

import yfinance as yf

import config


logger = logging.getLogger(__name__)

_dividend_cache: dict[str, tuple[dict | None, float]] = {}
_DIVIDEND_CACHE_TTL = 24 * 3600

def _fetch_dividend(ticker: str) -> dict | None:
    """Fetch next ex-dividend date + expected amount via yfinance.
    Expected amount = last historical dividend (best estimate without explicit guidance)."""
    try:
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        if not isinstance(cal, dict):
            return None
        ex_date = cal.get("Ex-Dividend Date")
        if hasattr(ex_date, "date"):
            ex_date = ex_date.date()
        elif isinstance(ex_date, str):
            try:
                ex_date = date.fromisoformat(ex_date[:10])
            except ValueError:
                return None
        if not isinstance(ex_date, date) or ex_date < date.today():
            return None
        divs = tk.dividends
        if divs is None or len(divs) == 0:
            return None
        expected = float(divs.iloc[-1])
        if expected <= 0:
            return None
        return {"ticker": ticker, "ex_date": str(ex_date), "expected_div": expected,
                "days_until": (ex_date - date.today()).days}
    except Exception as e:
        logger.debug("Dividend fetch failed for %s: %s", ticker, e)
        return None

def get_dividend_warnings(tickers: list[str], days_ahead: int = 7) -> list[dict]:
    """Return tickers with ex-dividend within days_ahead. Cached 24h.
    Each result: {ticker, ex_date, expected_div, days_until}.
    Use to warn about mechanical ex-div price drops near a stop-loss."""
    now = _time.time()
    results = []
    for ticker in tickers:
        cached = _dividend_cache.get(ticker)
        if cached and (now - cached[1]) < _DIVIDEND_CACHE_TTL:
            data = cached[0]
        else:
            data = _fetch_dividend(ticker)
            _dividend_cache[ticker] = (data, now)
        if data and data["days_until"] <= days_ahead:
            results.append(data)
    return sorted(results, key=lambda w: w["ex_date"])
