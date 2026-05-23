"""Risk-on/risk-off regime detection from SPY + VIX snapshot."""

"""Market data fetching: yfinance snapshots, technical indicators, per-ticker
cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, datetime, timedelta

import yfinance as yf

import config


logger = logging.getLogger(__name__)

def market_regime(market_ctx: dict) -> str:
    """Derive RISK_ON / RISK_OFF / NEUTRAL from SPY 200MA + VIX."""
    spy = market_ctx.get("SPY5.DE", {})
    vix = market_ctx.get("^VIX", {})
    price = spy.get("price")
    ma200 = spy.get("ma200")
    vix_val = vix.get("price")

    if not price or not ma200:
        regime = "UNKNOWN"
    elif price > ma200 * 1.01:
        regime = "RISK_ON"
    elif price < ma200 * 0.99:
        regime = "RISK_OFF"
    else:
        regime = "NEUTRAL"

    if vix_val:
        if vix_val > 30:
            regime += " ⚠️ VIX_EXTREME"
        elif vix_val > 20:
            regime += " ⚡ VIX_ELEVATED"

    return regime
