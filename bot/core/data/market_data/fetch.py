"""Per-ticker yfinance snapshot fetch + indicator composition + RS annotation."""

"""Market data fetching: yfinance snapshots, technical indicators, per-ticker
cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, datetime, timedelta

import yfinance as yf

import config


logger = logging.getLogger(__name__)

from core.data.market_data.indicators import (
    _compute_indicators, _count_consecutive_higher_lows, _session_fraction,
)
from core.data.market_data.isin import _isin_for_ticker

def _fetch_ticker(ticker: str) -> dict:
    """Fetch ticker snapshot: info, 1y daily (for weekly resample + MA200), intraday, indicators."""
    stock = yf.Ticker(ticker)
    info = stock.info
    hist_daily = stock.history(period="1y")
    try:
        hist_intraday = stock.history(period="1d", interval="15m")
    except Exception:
        hist_intraday = None

    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
    day_high = info.get("dayHigh") or info.get("regularMarketDayHigh")
    day_low = info.get("dayLow") or info.get("regularMarketDayLow")

    five_day = hist_daily.tail(5) if len(hist_daily) >= 5 else hist_daily
    five_day_high = float(five_day["High"].max()) if len(five_day) > 0 else None
    five_day_low = float(five_day["Low"].min()) if len(five_day) > 0 else None

    avg_volume = info.get("averageVolume")
    current_volume = info.get("volume") or info.get("regularMarketVolume")
    # Project intraday-cumulative volume to a full-day equivalent before the
    # ratio, so vol_ratio is time-of-day-neutral and comparable to averageVolume
    # (raw, it would climb all session — see _session_fraction).
    volume_ratio = None
    if avg_volume and current_volume:
        volume_ratio = (current_volume / _session_fraction(datetime.now())) / avg_volume

    bid, ask = info.get("bid"), info.get("ask")
    spread_pct = None
    if current_price and bid and ask:
        spread_pct = round((ask - bid) / current_price * 100, 3)

    # Analyst consensus (yfinance aggregates Reuters/Refinitiv feed).
    # Only surfaced if >=5 analysts cover the name — below that the mean is too noisy.
    analyst_count = info.get("numberOfAnalystOpinions")
    target_mean = info.get("targetMeanPrice") if (analyst_count or 0) >= 5 else None
    analyst_upside_pct = None
    if target_mean and current_price:
        analyst_upside_pct = round((target_mean - current_price) / current_price * 100, 1)

    # Pct distance from 52w high/low. Negative pct_below_52w_high = below 52w-high
    # (e.g. -8 = 8% unter ATH). Wert ~0 = an ATH (oft schon gelaufen). Wert -5 bis
    # -15 = potenzielles Base-Building. <-25 = tief im Drawdown.
    fw_high = info.get("fiftyTwoWeekHigh")
    fw_low = info.get("fiftyTwoWeekLow")
    pct_below_52w_high = (
        round((current_price - fw_high) / fw_high * 100, 2)
        if fw_high and current_price else None
    )
    pct_above_52w_low = (
        round((current_price - fw_low) / fw_low * 100, 2)
        if fw_low and current_price else None
    )

    snapshot = {
        "name": info.get("shortName", ticker),
        "price": current_price,
        "prev_close": prev_close,
        "change_pct": info.get("regularMarketChangePercent"),
        "day_high": day_high,
        "day_low": day_low,
        "5d_high": five_day_high,
        "5d_low": five_day_low,
        "52w_high": fw_high,
        "52w_low": fw_low,
        "pct_below_52w_high": pct_below_52w_high,
        "pct_above_52w_low": pct_above_52w_low,
        "volume": current_volume,
        "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
        "bid": bid,
        "ask": ask,
        "spread_pct": spread_pct,
        "analyst_target_mean": target_mean,
        "analyst_target_high": info.get("targetHighPrice") if (analyst_count or 0) >= 5 else None,
        "analyst_target_low": info.get("targetLowPrice") if (analyst_count or 0) >= 5 else None,
        "analyst_count": analyst_count,
        "analyst_rec_mean": info.get("recommendationMean") if (analyst_count or 0) >= 5 else None,
        "analyst_rec_key": info.get("recommendationKey") if (analyst_count or 0) >= 5 else None,
        "analyst_upside_pct": analyst_upside_pct,
    }
    snapshot.update(_compute_indicators(hist_daily, hist_intraday))

    # Live-quote overlay from Lang & Schwarz Tradecenter (ls-tc.de). Fresher
    # than yfinance (15min delay) and matches what TR shows the user. Fail-soft:
    # any error keeps yfinance baseline. ISIN comes from yfinance info.
    try:
        from core.data.livefeed import get_live_quote
        isin = info.get("isin") or _isin_for_ticker(ticker, stock)
        if isin:
            lq = get_live_quote(isin)
            if lq and lq.get("price"):
                snapshot["live_price"] = lq["price"]
                snapshot["live_bid"] = lq.get("bid")
                snapshot["live_ask"] = lq.get("ask")
                snapshot["live_ts"] = lq.get("ts")
                snapshot["live_change_pct"] = lq.get("change_pct")
                snapshot["live_market_status"] = lq.get("market_status")
                snapshot["live_source"] = lq.get("source")
                # Overwrite price + bid/ask with live values so downstream
                # (events.py trigger checks, analyzer.py prompt) gets fresh data.
                snapshot["price"] = lq["price"]
                if lq.get("bid"):
                    snapshot["bid"] = lq["bid"]
                if lq.get("ask"):
                    snapshot["ask"] = lq["ask"]
                if snapshot.get("bid") and snapshot.get("ask") and snapshot["price"]:
                    snapshot["spread_pct"] = round(
                        (snapshot["ask"] - snapshot["bid"]) / snapshot["price"] * 100, 3
                    )
    except Exception as e:
        logger.debug("LS-TC overlay failed for %s: %s", ticker, e)

    # Base-Quality score (0-10) — structural-repair signal complementing the
    # confluence (momentum/trend) score. Primary quality indicator for the
    # swing-low setup family per the 2026-05-20 swing-structure-filter
    # manifest (point 8). Imported locally to avoid module-load cycle if
    # core.portfolio ever imports market_data.
    try:
        from core.portfolio import compute_base_quality
        bq = compute_base_quality(snapshot)
        snapshot["base_quality_score"] = bq["score"]
        snapshot["base_quality_items"] = bq["items"]
    except Exception as e:
        logger.debug("base_quality calc failed for %s: %s", ticker, e)

    return snapshot

def _annotate_relative_strength(data: dict):
    """Add `rs_20d_vs_index_pct` to each snapshot (ticker_perf − index_perf, in pp).
    Index fetched on-demand if not present in batch (cached so cheap)."""
    # Lazy import — cache.py imports from this module, so a top-level import
    # would be circular. Function-scope keeps the cycle deferred.
    from core.data.market_data.cache import _market_cache

    index_ticker = config.RS_INDEX_TICKER
    index_snap = data.get(index_ticker)
    if not isinstance(index_snap, dict) or index_snap.get("error"):
        # Pull index separately (will hit cache if recently fetched elsewhere).
        cached = _market_cache.get(index_ticker)
        if cached and (_time.time() - cached[1]) < config.MARKET_DATA_CACHE_TTL_SECONDS:
            index_snap = cached[0]
        else:
            try:
                index_snap = _fetch_ticker(index_ticker)
                _market_cache[index_ticker] = (index_snap, _time.time())
            except Exception:
                return
    index_perf = index_snap.get("perf_20d_pct") if isinstance(index_snap, dict) else None
    if not isinstance(index_perf, (int, float)):
        return
    for ticker, snap in data.items():
        if not isinstance(snap, dict) or snap.get("error"):
            continue
        if ticker == index_ticker:
            continue
        ticker_perf = snap.get("perf_20d_pct")
        if isinstance(ticker_perf, (int, float)):
            snap["rs_20d_vs_index_pct"] = round(ticker_perf - index_perf, 2)
