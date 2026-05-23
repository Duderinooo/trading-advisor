"""Intraday 15min-bar cache + first-touch resolver.

Used to disambiguate cases where a daily bar has both `low ≤ SL` AND
`high ≥ TP1` — daily granularity can't tell which level the price reached first.
Pulling 15min bars resolves the order.

yfinance constraint: intraday `interval=15m` data is only available for the
last ~60 days. Older requests fail. The cache is fail-soft: if intraday is
unavailable, callers get `None` and fall back to "ambiguous" classification.

Storage: data_cache/intraday/{ticker}.jsonl — one line per 15min bar.
Schema:  {"ts": "YYYY-MM-DD HH:MM", "date": "YYYY-MM-DD", "open": ..., "high": ...,
          "low": ..., "close": ..., "volume": int}
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf


logger = logging.getLogger(__name__)


_CACHE_DIR = (
    Path(os.path.dirname(os.path.abspath(__file__)))
    / ".." / ".." / "data_cache" / "intraday"
).resolve()


def _ensure_dir() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _path_for(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.replace('/', '_')}.jsonl"


def _load_cache(ticker: str) -> dict[str, dict]:
    """Read existing cache for `ticker` → {ts_str: bar_dict}."""
    path = _path_for(ticker)
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("ts")
                if ts:
                    out[ts] = rec
    except Exception:
        logger.exception("Intraday cache read failed for %s", ticker)
    return out


def _write_cache(ticker: str, bars: dict[str, dict]) -> None:
    """Persist cache as ts-sorted JSONL. Atomic tmp + rename."""
    _ensure_dir()
    path = _path_for(ticker)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        for ts in sorted(bars.keys()):
            f.write(json.dumps(bars[ts]) + "\n")
    tmp.replace(path)


def _fetch_yfinance_intraday(
    ticker: str, target_date: date,
) -> dict[str, dict]:
    """Pull 15min bars for a single trading day. Window = [target, target+1d]."""
    try:
        # yfinance: interval='15m' needs end exclusive; period anchored to today.
        # Limit: 60d back. Caller should not request older.
        hist = yf.Ticker(ticker).history(
            start=target_date.isoformat(),
            end=(target_date + timedelta(days=1)).isoformat(),
            interval="15m",
        )
    except Exception:
        logger.warning("yfinance intraday fetch failed for %s on %s",
                       ticker, target_date)
        return {}
    if hist is None or hist.empty:
        return {}
    out: dict[str, dict] = {}
    for ts, row in hist.iterrows():
        try:
            ts_str = ts.strftime("%Y-%m-%d %H:%M")
            out[ts_str] = {
                "ts": ts_str,
                "date": ts.date().isoformat(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row.get("Volume", 0) or 0),
            }
        except Exception:
            continue
    return out


def get_intraday_bars(ticker: str, target_date: date) -> list[dict]:
    """Return all 15min bars for `target_date` (ascending). Auto-fetches missing.
    Empty list if no data (e.g. weekend, holiday, beyond 60-day limit)."""
    cached = _load_cache(ticker)
    target_str = target_date.isoformat()
    day_bars = sorted(
        [b for b in cached.values() if b.get("date") == target_str],
        key=lambda b: b["ts"],
    )
    if day_bars:
        return day_bars

    fetched = _fetch_yfinance_intraday(ticker, target_date)
    if not fetched:
        return []
    cached.update(fetched)
    _write_cache(ticker, cached)
    return sorted(
        [b for b in cached.values() if b.get("date") == target_str],
        key=lambda b: b["ts"],
    )


def first_touch_intraday(
    ticker: str, target_date: date, stop_loss: float, take_profit: float,
) -> str | None:
    """Walks 15min bars in chronological order. Returns whichever level was
    touched first:
    - "sl"  → low ≤ stop_loss before high ≥ take_profit
    - "tp"  → high ≥ take_profit before low ≤ stop_loss
    - None  → neither touched intraday, OR intraday data unavailable

    Same-bar ambiguity within 15min still possible (rare; treated as None).
    """
    bars = get_intraday_bars(ticker, target_date)
    if not bars:
        return None
    for bar in bars:
        touch_sl = bar["low"] <= stop_loss
        touch_tp = bar["high"] >= take_profit
        if touch_sl and touch_tp:
            return None  # 15min bar still ambiguous
        if touch_sl:
            return "sl"
        if touch_tp:
            return "tp"
    return None
