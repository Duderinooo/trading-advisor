"""Portfolio-level metrics computed for the dashboard.

Currently: pairwise return-correlation matrix across open positions.
"""

import logging
from datetime import datetime

import config
from core.data.market_data import get_returns


logger = logging.getLogger(__name__)


def compute_correlation_snapshot(portfolio: dict) -> dict | None:
    """Pairwise return-correlation matrix over open positions for the dashboard.

    Returns None if <2 open trades or returns fetch fails. Used by morning
    analyzer AND /confirm path so the dashboard heatmap fills in immediately
    after opening a 2nd position (prev: stayed empty until next morning).
    """
    _open = [t["ticker"] for t in portfolio.get("open_trades", []) if t.get("ticker")]
    if len(_open) < 2:
        return None
    try:
        _ret = get_returns(_open, days=config.CORRELATION_LOOKBACK_DAYS)
    except Exception:
        logger.exception("Correlation snapshot failed (returns fetch)")
        return None
    _m: dict[str, dict[str, float]] = {}
    for a in _open:
        sa = _ret.get(a)
        if sa is None:
            continue
        _m[a] = {}
        for b in _open:
            if a == b:
                _m[a][b] = 1.0
                continue
            sb = _ret.get(b)
            if sb is None:
                continue
            try:
                c = float(sa.corr(sb))
                if c == c:  # NaN check
                    _m[a][b] = round(c, 2)
            except Exception:
                continue
    return {
        "tickers": _open,
        "matrix": _m,
        "lookback_days": config.CORRELATION_LOOKBACK_DAYS,
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
