"""Market data fetching: yfinance snapshots, technical indicators,
per-ticker cache, earnings calendar, news headlines."""

import logging
import time as _time
from datetime import date, timedelta

import yfinance as yf

import config

logger = logging.getLogger(__name__)

# Per-ticker market-data cache: ticker -> (data_dict, fetched_at_epoch)
_market_cache: dict[str, tuple[dict, float]] = {}

# Per-ticker earnings cache: ticker -> (results_list, fetched_at_epoch)
_earnings_cache: dict[str, tuple[list, float]] = {}
_EARNINGS_CACHE_TTL = 6 * 3600

# Per-ticker dividend cache: ticker -> ({ex_date, expected_div} | None, fetched_at_epoch)
_dividend_cache: dict[str, tuple[dict | None, float]] = {}
_DIVIDEND_CACHE_TTL = 24 * 3600


# ---------- Indicators ----------

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

    except (KeyError, ValueError, IndexError) as e:
        logger.debug("Indicator calc failed: %s", e)

    return out


# ---------- Fetching ----------

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
    volume_ratio = current_volume / avg_volume if avg_volume and current_volume else None

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

    snapshot = {
        "name": info.get("shortName", ticker),
        "price": current_price,
        "prev_close": prev_close,
        "change_pct": info.get("regularMarketChangePercent"),
        "day_high": day_high,
        "day_low": day_low,
        "5d_high": five_day_high,
        "5d_low": five_day_low,
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
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
    return snapshot


def get_market_data(tickers: list[str], ttl_seconds: int = None) -> dict:
    """Fetch current market data, using per-ticker cache. Cache hits skip yfinance entirely."""
    if ttl_seconds is None:
        ttl_seconds = config.MARKET_DATA_CACHE_TTL_SECONDS

    now = _time.time()
    data = {}

    for ticker in tickers:
        cached = _market_cache.get(ticker)
        if cached and (now - cached[1]) < ttl_seconds:
            data[ticker] = cached[0]
            continue

        try:
            fresh = _fetch_ticker(ticker)
            _market_cache[ticker] = (fresh, now)
            data[ticker] = fresh
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", ticker, e)
            data[ticker] = {"error": str(e)}

    # Post-process: relative strength vs index (20d perf delta).
    # Index ticker fetched on-demand if not in batch.
    _annotate_relative_strength(data)
    return data


def _annotate_relative_strength(data: dict):
    """Add `rs_20d_vs_index_pct` to each snapshot (ticker_perf − index_perf, in pp).
    Index fetched on-demand if not present in batch (cached so cheap)."""
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


def invalidate_market_cache():
    """Force next get_market_data call to re-fetch from yfinance."""
    _market_cache.clear()


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

def fetch_news(tickers: list[str], limit_per_ticker: int = 3) -> dict[str, list[str]]:
    """Fetch recent news headlines per ticker via yfinance. Best-effort, silent on failure."""
    result = {}
    for ticker in tickers:
        try:
            news = yf.Ticker(ticker).news or []
            headlines = [item.get("title", "") for item in news[:limit_per_ticker] if item.get("title")]
            if headlines:
                result[ticker] = headlines
        except Exception:
            pass
    return result


# ---------- Market regime ----------

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
