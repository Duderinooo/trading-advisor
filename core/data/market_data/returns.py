"""Historical return queries for correlation + alpha-vs-beta attribution."""

"""Market data fetching: yfinance snapshots, technical indicators, per-ticker
cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, datetime, timedelta

import yfinance as yf

import config


logger = logging.getLogger(__name__)

def get_period_return(ticker: str, start_date: str, end_date: str) -> float | None:
    """Total return % between two dates (inclusive). Returns None if data unavailable.

    `start_date` / `end_date`: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'. Used for SPY-attribution
    on closed_trades — same period as the trade's hold-window."""
    try:
        sd = start_date[:10]
        ed = end_date[:10]
        # Pad +1 day so end_date is inclusive when yfinance treats end as exclusive.
        ed_dt = datetime.strptime(ed, "%Y-%m-%d") + timedelta(days=1)
        hist = yf.Ticker(ticker).history(start=sd, end=ed_dt.strftime("%Y-%m-%d"))
        if hist.empty or len(hist) < 2:
            return None
        first = float(hist["Close"].iloc[0])
        last = float(hist["Close"].iloc[-1])
        if first <= 0:
            return None
        return round((last - first) / first * 100, 2)
    except Exception:
        logger.debug("Period return fetch failed for %s %s→%s", ticker, start_date, end_date)
        return None


# ---------- Returns (for correlation / RS calculations) ----------

def get_returns(tickers: list[str], days: int = 60):
    """Per-ticker daily-return series (pandas) over last `days`. Used for correlation gate.
    Returns dict[ticker -> pd.Series]. Missing/short series omitted silently."""
    out = {}
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period=f"{days + 10}d")
            if len(hist) < 20:
                continue
            returns = hist["Close"].pct_change().dropna().tail(days)
            if len(returns) >= 20:
                out[t] = returns
        except Exception:
            logger.debug("Returns fetch failed for %s", t)
    return out


# ---------- Earnings ----------
