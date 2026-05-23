"""core.data.market_data — yfinance fetching + indicators + cache + earnings + news.

Split from monolithic core/data/market_data.py (681 LOC). Public API
preserved: every previously-importable name re-exports here.
"""

from core.data.market_data.cache import get_market_data, invalidate_market_cache
from core.data.market_data.dividends import get_dividend_warnings
from core.data.market_data.earnings import get_earnings_warnings
from core.data.market_data.indicators import (
    _MIN_SESSION_FRACTION, _compute_indicators,
    _count_consecutive_higher_lows, _rsi, _session_fraction,
)
from core.data.market_data.news import fetch_news
from core.data.market_data.regime import market_regime
from core.data.market_data.returns import get_period_return, get_returns

__all__ = [
    "get_market_data", "invalidate_market_cache",
    "get_earnings_warnings", "get_dividend_warnings", "fetch_news",
    "market_regime",
    "get_returns", "get_period_return",
    # Indicator helpers (private but covered by tests)
    "_compute_indicators", "_count_consecutive_higher_lows", "_rsi",
    "_session_fraction", "_MIN_SESSION_FRACTION",
]
