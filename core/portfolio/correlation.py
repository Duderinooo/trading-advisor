"""Pairwise return correlation for the correlation-cluster gate."""

import logging


logger = logging.getLogger(__name__)


def compute_correlations(returns_by_ticker: dict, candidate: str) -> dict[str, float]:
    """Pairwise correlation of `candidate` daily returns vs each other ticker.
    `returns_by_ticker` is dict[ticker -> pandas.Series of daily pct_change()].
    Returns dict[other_ticker -> corr_float]. Missing/short series omitted.
    """
    cand = returns_by_ticker.get(candidate)
    if cand is None or len(cand.dropna()) < 20:
        return {}
    out = {}
    for t, series in returns_by_ticker.items():
        if t == candidate or series is None or len(series.dropna()) < 20:
            continue
        try:
            c = float(cand.corr(series))
            if c == c:  # not NaN
                out[t] = round(c, 2)
        except Exception:
            continue
    return out


# ---------- Hit-rate stats ----------
