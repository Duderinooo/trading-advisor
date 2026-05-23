"""Technical indicator computation: RSI, MACD, MA, BB, ATR, VWAP, vol-ratio,
higher-lows base-structure detector. Pure (no IO)."""

"""Market data fetching: yfinance snapshots, technical indicators, per-ticker
cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, datetime, timedelta

import yfinance as yf

import config


logger = logging.getLogger(__name__)

def _rsi(series, period: int = 14):
    """Classic 14-period RSI via Wilder's smoothing."""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period, min_periods=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period, min_periods=period).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def _compute_indicators(daily_hist, intraday_hist) -> dict:
    """Compute standard technical indicators + weekly trend. Returns {} if insufficient data."""
    out = {}
    try:
        if len(daily_hist) >= 50:
            close = daily_hist["Close"]
            ma20 = close.rolling(20).mean().iloc[-1]
            ma50 = close.rolling(50).mean().iloc[-1]
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()

            rsi_series = _rsi(close)
            rsi_val = rsi_series.iloc[-1] if not rsi_series.dropna().empty else None

            bb_mid = close.rolling(20).mean()
            bb_std = close.rolling(20).std()
            bb_upper = (bb_mid + 2 * bb_std).iloc[-1]
            bb_lower = (bb_mid - 2 * bb_std).iloc[-1]

            ma200 = close.rolling(200).mean().iloc[-1] if len(daily_hist) >= 200 else None

            # ATR14
            high = daily_hist["High"]
            low = daily_hist["Low"]
            prev_close = close.shift(1)
            import pandas as pd
            tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
            atr14 = tr.rolling(14).mean().iloc[-1]
            current_px = close.iloc[-1]
            atr14_pct = round(float(atr14) / float(current_px) * 100, 2) if current_px else None

            import math
            out.update({
                "ma20": round(float(ma20), 2),
                "ma50": round(float(ma50), 2),
                "ma200": round(float(ma200), 2) if ma200 is not None and not math.isnan(float(ma200)) else None,
                "rsi14": round(float(rsi_val), 1) if rsi_val is not None else None,
                "macd": round(float(macd_line.iloc[-1]), 3),
                "macd_signal": round(float(signal_line.iloc[-1]), 3),
                "macd_hist": round(float(macd_line.iloc[-1] - signal_line.iloc[-1]), 3),
                "bb_upper": round(float(bb_upper), 2),
                "bb_lower": round(float(bb_lower), 2),
                "atr14": round(float(atr14), 3),
                "atr14_pct": atr14_pct,
            })

            # Range-Tightness — Pre-Breakout-Detektor.
            # range_20d_pct = (20d-High − 20d-Low) / current_price × 100.
            # range_compression = range_20d / range_60d. <0.4 = Volatility-Squeeze
            # (20-Tage-Range deutlich enger als 60-Tage-Norm) → "coiled spring",
            # häufig vor Range-Expansion. Nur Signal, kein Gate — Claude entscheidet.
            try:
                if len(daily_hist) >= 20 and current_px:
                    h20 = float(daily_hist["High"].tail(20).max())
                    l20 = float(daily_hist["Low"].tail(20).min())
                    r20 = (h20 - l20) / float(current_px) * 100
                    out["range_20d_pct"] = round(r20, 2)
                    if len(daily_hist) >= 60:
                        h60 = float(daily_hist["High"].tail(60).max())
                        l60 = float(daily_hist["Low"].tail(60).min())
                        r60 = (h60 - l60) / float(current_px) * 100
                        if r60 > 0:
                            out["range_compression"] = round(r20 / r60, 2)
            except (KeyError, ValueError, IndexError) as e:
                logger.debug("Range-tightness calc failed: %s", e)

            # Weekly timeframe (resample daily → weekly) — confirms trend direction.
            # Rule: don't go long against weekly downtrend.
            try:
                weekly = daily_hist["Close"].resample("W").last().dropna()
                if len(weekly) >= 30:
                    wk_ma20 = weekly.rolling(20).mean().iloc[-1]
                    wk_ma50 = weekly.rolling(50).mean().iloc[-1] if len(weekly) >= 50 else None
                    wk_last = float(weekly.iloc[-1])
                    wk_rsi = _rsi(weekly).iloc[-1] if len(weekly) >= 15 else None

                    if wk_ma50 is not None:
                        if wk_last > wk_ma20 > wk_ma50:
                            wk_trend = "UP"
                        elif wk_last < wk_ma20 < wk_ma50:
                            wk_trend = "DOWN"
                        else:
                            wk_trend = "MIXED"
                    else:
                        wk_trend = "UP" if wk_last > wk_ma20 else "DOWN"

                    out.update({
                        "wk_ma20": round(float(wk_ma20), 2),
                        "wk_ma50": round(float(wk_ma50), 2) if wk_ma50 is not None else None,
                        "wk_trend": wk_trend,
                        "wk_rsi14": round(float(wk_rsi), 1) if wk_rsi is not None else None,
                    })
            except Exception as e:
                logger.debug("Weekly indicator calc failed: %s", e)

        # VWAP from intraday data (today's session)
        if intraday_hist is not None and len(intraday_hist) > 0:
            typical = (intraday_hist["High"] + intraday_hist["Low"] + intraday_hist["Close"]) / 3
            vol = intraday_hist["Volume"]
            vwap = (typical * vol).cumsum() / vol.cumsum().replace(0, 1e-9)
            vwap_val = float(vwap.iloc[-1])
            out["vwap"] = round(vwap_val, 2)
            # Deviation from VWAP in ATR units — >2 = flash-spike / exhaustion zone
            _atr = out.get("atr14")
            try:
                _cur = float(intraday_hist["Close"].iloc[-1])
                if _atr and _atr > 0:
                    out["vwap_dev_atr"] = round((_cur - vwap_val) / _atr, 2)
            except Exception:
                pass

            today_open = float(intraday_hist["Open"].iloc[0])
            today_close = float(intraday_hist["Close"].iloc[-1])
            out["day_open"] = round(today_open, 2)
            out["pct_from_open"] = round((today_close - today_open) / today_open * 100, 2)
            out["intraday_high"] = round(float(intraday_hist["High"].max()), 2)
            out["intraday_low"] = round(float(intraday_hist["Low"].min()), 2)

        # 20d return (used for relative-strength gate vs index).
        if len(daily_hist) >= 21:
            close = daily_hist["Close"]
            px_20d_ago = float(close.iloc[-21])
            px_now = float(close.iloc[-1])
            if px_20d_ago > 0:
                out["perf_20d_pct"] = round((px_now - px_20d_ago) / px_20d_ago * 100, 2)

        # Higher-Lows base-formation signal (0-4 over last 5 daily bars).
        # Sonnet uses this as a positive confirm for the swing-structure setup
        # family ("Higher Lows entwickeln sich" — manifest point 7 / 9).
        if len(daily_hist) >= 5:
            try:
                recent_lows = [float(x) for x in daily_hist["Low"].tail(5).tolist()]
                out["higher_lows_5d"] = _count_consecutive_higher_lows(recent_lows)
            except (TypeError, ValueError):
                pass

    except (KeyError, ValueError, IndexError) as e:
        logger.debug("Indicator calc failed: %s", e)

    return out


# ---------- Fetching ----------

# Xetra regular session 09:00–17:30 CET. yfinance `volume` is intraday-cumulative
# (volume-so-far), not a completed day — comparing it raw to averageVolume makes
# vol_ratio climb through the session (2026-05-19: PUM vol_ratio 0.12→0.17 all
# day, breakout volume-gate structurally unreachable before late afternoon).
_XETRA_OPEN_MIN = 9 * 60
_XETRA_CLOSE_MIN = 17 * 60 + 30
_MIN_SESSION_FRACTION = 0.15  # floor — don't over-project on thin early data

def _session_fraction(now: datetime) -> float:
    """Fraction of the Xetra regular session elapsed at local time `now`.

    Returns 1.0 outside session hours / on weekends — there the yfinance volume
    figure is a completed day and needs no projection. During the session the
    fraction is floored at _MIN_SESSION_FRACTION so the first ~75min don't blow
    the projection up on a tiny denominator (early understatement is
    conservative — it defers a volume-confirm, never false-passes one).
    """
    if now.weekday() >= 5:
        return 1.0
    cur = now.hour * 60 + now.minute
    if cur <= _XETRA_OPEN_MIN or cur >= _XETRA_CLOSE_MIN:
        return 1.0
    frac = (cur - _XETRA_OPEN_MIN) / (_XETRA_CLOSE_MIN - _XETRA_OPEN_MIN)
    return max(_MIN_SESSION_FRACTION, frac)

def _count_consecutive_higher_lows(lows: list[float]) -> int:
    """Count consecutive higher-lows starting from the second element.

    Used as a base-formation signal — `Higher Lows entwickeln sich` per the
    swing-structure-filter manifest. Each subsequent low strictly greater than
    its predecessor counts; the streak breaks on the first non-higher low.

    Examples:
        [10, 11, 12, 13, 14] → 4 (every step strictly higher)
        [10, 11, 12, 11, 13] → 2 (streak breaks at index 3)
        [10, 10, 11]         → 0 (10 == 10 breaks immediately)
        [10]                 → 0 (no pair to compare)
        []                   → 0
    """
    if len(lows) < 2:
        return 0
    count = 0
    for i in range(1, len(lows)):
        if lows[i] > lows[i - 1]:
            count += 1
        else:
            break
    return count
