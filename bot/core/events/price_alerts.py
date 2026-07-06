"""Price-alert detection: significant intraday moves on watchlist/commodities.

Pre-filter: only emit if ticker has an active watch-level OR move is
≥STRONG_PRICE_ALERT_PERCENT. Per-(direction, ticker) per-day dedup.
"""

import logging
from datetime import date

import config
from core.events.types import EventType
from core.data.market_data import get_market_data
from core.portfolio import (
    portfolio_lock, load_portfolio, save_portfolio,
    exit_suppressed_tickers,
)
from core.portfolio.dedup_store import load_dedup, save_dedup


logger = logging.getLogger(__name__)


def check_price_alerts() -> list[dict]:
    """Return DROP / RISE alerts with per-(direction, ticker)-per-day dedup."""
    with portfolio_lock:
        portfolio = load_portfolio()
        today = str(date.today())

        dedup = load_dedup()
        triggered = [
            t for t in dedup.get("triggered_price_alerts", [])
            if t.get("date") == today
        ]
        triggered_keys = {t["key"] for t in triggered}

        alerts: list[dict] = []
        excluded = set(config.EXCLUDED_TICKERS)
        suppressed = {t.upper() for t in exit_suppressed_tickers(portfolio)}
        watch_tickers = [
            t for t in config.WATCHLIST + config.COMMODITIES
            if t not in excluded and t.upper() not in suppressed
        ]
        market_data = get_market_data(watch_tickers)

        watch_level_set = {
            w["ticker"] for w in portfolio.get("watch_levels", []) if w.get("ticker")
        }

        for ticker, data in market_data.items():
            if "error" in data or ticker in excluded:
                continue
            if ticker.upper() in suppressed:
                continue
            change = data.get("change_pct")
            if change is None:
                continue

            direction = None
            if change <= -config.PRICE_DROP_ALERT_PERCENT:
                direction = EventType.DROP
            elif change >= config.PRICE_RISE_ALERT_PERCENT:
                direction = EventType.RISE
            if not direction:
                continue

            has_watch = ticker in watch_level_set
            is_strong = abs(change) >= config.STRONG_PRICE_ALERT_PERCENT
            if not has_watch and not is_strong:
                logger.debug(
                    "Price alert filtered: %s %+.1f%% (no watch-level, below %.1f%% strong threshold)",
                    ticker, change, config.STRONG_PRICE_ALERT_PERCENT,
                )
                continue

            key = f"{direction}_{ticker}"
            if key in triggered_keys:
                continue

            alerts.append({
                "type": direction,
                "ticker": ticker,
                "name": data.get("name", ticker),
                "change": change,
                "price": data.get("price"),
            })
            triggered.append({"key": key, "date": today})
            triggered_keys.add(key)

        if alerts:
            dedup["triggered_price_alerts"] = triggered
            save_dedup(dedup)

        return alerts


def check_coffee_futures_levels() -> list[dict]:
    """Level-break alerts on ICE Coffee C futures (KC=F) for the OD7B.DE
    coffee-ETC thesis (2026-07-06). Deterministic rule, no LLM: price >=
    resistance → COFFEE_BREAK_UP, price <= support → COFFEE_BREAK_DOWN.
    Once per (level, day) via the triggered_price_alerts dedup. Info-only —
    the ETC is held outside the bot's gate pipeline."""
    today = str(date.today())
    dedup = load_dedup()
    triggered = [
        t for t in dedup.get("triggered_price_alerts", [])
        if t.get("date") == today
    ]
    triggered_keys = {t["key"] for t in triggered}

    kc = config.COFFEE_FUTURES_TICKER
    data = get_market_data([kc]).get(kc, {})
    price = data.get("price")
    if "error" in data or not isinstance(price, (int, float)):
        return []

    checks = [
        ("COFFEE_BREAK_UP", price >= config.COFFEE_FUTURES_RESISTANCE,
         config.COFFEE_FUTURES_RESISTANCE),
        ("COFFEE_BREAK_DOWN", price <= config.COFFEE_FUTURES_SUPPORT,
         config.COFFEE_FUTURES_SUPPORT),
    ]
    alerts: list[dict] = []
    for key, hit, level in checks:
        if not hit or key in triggered_keys:
            continue
        alerts.append({
            "type": key,
            "ticker": kc,
            "price": price,
            "level": level,
            "change": data.get("change_pct"),
        })
        triggered.append({"key": key, "date": today})
        triggered_keys.add(key)

    if alerts:
        dedup["triggered_price_alerts"] = triggered
        save_dedup(dedup)
    return alerts
