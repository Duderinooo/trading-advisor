"""Historical daily-bar cache for replay + counterfactual analysis.

Persists yfinance daily OHLC per ticker in a flat JSONL file. Lookups read
from disk and lazy-fetch missing windows from yfinance. The cache is grep-able
and append-only; no database dependency.

Storage: data_cache/daily/{ticker}.jsonl — one line per bar.
Schema:  {"date": "YYYY-MM-DD", "open": f, "high": f, "low": f, "close": f, "volume": int}

CLI:
    ./venv/bin/python -m core.data.historical --tickers BAS.DE,DBK.DE \\
        --start 2026-01-01 --end 2026-05-23

Use cases:
- core/llm/telemetry/outcomes.py: multi-day window for blocked-trade counterfactual
  (current 1-day version misses TPs that hit on day+2 / day+3).
- core/replay/walkthrough.py: simulate trade trajectory bar-by-bar.
- Future parameter-sweep: replay full days against modified config.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf


logger = logging.getLogger(__name__)


_CACHE_DIR = (
    Path(os.path.dirname(os.path.abspath(__file__))) / ".." / ".." / "data_cache" / "daily"
).resolve()


def _ensure_dir() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _path_for(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.replace('/', '_')}.jsonl"


def _load_cache(ticker: str) -> dict[str, dict]:
    """Read existing cache for `ticker` → {date_str: bar_dict}."""
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
                d = rec.get("date")
                if d:
                    out[d] = rec
    except Exception:
        logger.exception("Cache read failed for %s", ticker)
    return out


def _write_cache(ticker: str, bars: dict[str, dict]) -> None:
    """Persist cache as sorted-by-date JSONL. Atomic via tmp+rename."""
    _ensure_dir()
    path = _path_for(ticker)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        for d in sorted(bars.keys()):
            f.write(json.dumps(bars[d]) + "\n")
    tmp.replace(path)


def _fetch_yfinance(ticker: str, start: date, end: date) -> dict[str, dict]:
    """Pull daily bars for [start, end] via yfinance. Returns dict keyed by ISO date.
    Empty dict on failure."""
    try:
        # yfinance end is exclusive; bump by 1d to include `end`.
        hist = yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
        )
    except Exception:
        logger.warning("yfinance fetch failed for %s [%s..%s]", ticker, start, end)
        return {}
    if hist is None or hist.empty:
        return {}
    out: dict[str, dict] = {}
    for ts, row in hist.iterrows():
        try:
            d = ts.date().isoformat()
            out[d] = {
                "date": d,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row.get("Volume", 0) or 0),
            }
        except Exception:
            continue
    return out


def cache_daily_bars(
    tickers: list[str], start: date, end: date, *, force_refresh: bool = False,
) -> dict[str, int]:
    """Fetch + persist daily bars for each ticker. Returns {ticker: bar_count}.
    Skip-cached: dates already present in cache aren't refetched unless force_refresh."""
    summary: dict[str, int] = {}
    for ticker in tickers:
        cached = {} if force_refresh else _load_cache(ticker)
        target_dates = set()
        d = start
        while d <= end:
            if d.weekday() < 5:  # weekday only — skip Sat/Sun
                target_dates.add(d.isoformat())
            d += timedelta(days=1)
        missing = target_dates - set(cached.keys())
        if missing:
            fetched = _fetch_yfinance(ticker, start, end)
            cached.update(fetched)
            _write_cache(ticker, cached)
        summary[ticker] = len(cached)
    return summary


def get_daily_bar(
    ticker: str, target: date, *, auto_fetch: bool = True,
) -> dict | None:
    """Lookup a single daily bar. If not cached and auto_fetch, pulls a 7-day
    window around target_date. Returns None if neither cache nor yfinance has data."""
    cached = _load_cache(ticker)
    key = target.isoformat()
    if key in cached:
        return cached[key]
    if not auto_fetch:
        return None
    # Pull a small window in case there's a gap (weekend, holiday).
    fetched = _fetch_yfinance(ticker, target - timedelta(days=2), target + timedelta(days=2))
    if not fetched:
        return None
    cached.update(fetched)
    _write_cache(ticker, cached)
    return cached.get(key)


def get_daily_bars_range(
    ticker: str, start: date, end: date, *, auto_fetch: bool = True,
) -> list[dict]:
    """Return all cached bars in [start, end] (weekdays + actual trading days).
    Auto-fetches missing windows. List sorted ascending by date."""
    cached = _load_cache(ticker)
    keys_in_range = sorted(
        d for d in cached.keys()
        if start.isoformat() <= d <= end.isoformat()
    )
    # If cache covers fewer than the expected weekday count by more than half,
    # auto-fetch (signal that we never cached this window).
    expected_weekdays = sum(
        1 for i in range((end - start).days + 1)
        if (start + timedelta(days=i)).weekday() < 5
    )
    if auto_fetch and len(keys_in_range) < max(1, expected_weekdays // 2):
        fetched = _fetch_yfinance(ticker, start, end)
        if fetched:
            cached.update(fetched)
            _write_cache(ticker, cached)
            keys_in_range = sorted(
                d for d in cached.keys()
                if start.isoformat() <= d <= end.isoformat()
            )
    return [cached[k] for k in keys_in_range]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Cache yfinance daily bars to data_cache/daily/.",
    )
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="Refresh existing cache")
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    summary = cache_daily_bars(tickers, start, end, force_refresh=args.force)
    for t, n in sorted(summary.items()):
        print(f"  {t}: {n} bars cached")
    print(f"\nTotal: {sum(summary.values())} bars across {len(summary)} tickers")


if __name__ == "__main__":
    _main()
