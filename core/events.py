"""Event detection + trade state transitions:
- News scanning (geopolitical + stock-specific)
- Watch-level hit detection
- Price-alert detection with pre-filter
- Stop-loss / take-profit / trailing-stop checks
"""

import hashlib
import logging
from datetime import datetime, date

import yfinance as yf

import config
from core.portfolio import portfolio_lock, load_portfolio, save_portfolio, maintain_drawdown_state
from core.market_data import get_market_data
from core.api_usage import get_minutes_since_last_analysis

logger = logging.getLogger(__name__)


# ---------- News event detection ----------

_STOCK_NEWS_KEYWORDS = [
    # English
    "earnings", "beat", "miss", "guidance", "raised", "lowered", "cut",
    "upgrade", "downgrade", "outperform", "underperform",
    "acquisition", "merger", "takeover", "buyout",
    "fda", "approval", "approved", "rejected", "recall",
    "ceo", "resign", "fired", "arrested", "investigation", "lawsuit", "fraud",
    "layoff", "restructur", "bankrupt", "default",
    "dividend", "buyback", "split",
    "revenue", "profit", "loss", "forecast", "outlook",
    # German
    "übernahme", "quartalsergebnis", "gewinnwarnung", "prognose",
    "insolvenz", "stellenabbau", "rücktritt",
]


def check_news_events() -> list[dict]:
    """Scan news for all watchlist/open/commodity tickers.
    Returns new actionable events not seen before today.
    Persists seen article hashes in portfolio.json (under lock)."""
    with portfolio_lock:
        portfolio = load_portfolio()
        today = str(date.today())
        seen_today = set(portfolio.get("seen_news", {}).get(today, []))

        open_tickers = [t["ticker"] for t in portfolio.get("open_trades", [])]
        scan_tickers = list(dict.fromkeys(
            list(config.MARKET_INDICATORS) + open_tickers + config.WATCHLIST + config.COMMODITIES
        ))

        events = []
        new_hashes = []

        for ticker in scan_tickers:
            try:
                items = yf.Ticker(ticker).news or []
            except Exception:
                continue

            for item in items:
                title = item.get("title", "")
                if not title:
                    continue

                h = hashlib.md5(title.lower().encode()).hexdigest()[:16]
                if h in seen_today:
                    continue
                new_hashes.append(h)

                title_lower = title.lower()

                triggered = [
                    comm for comm, kws in config.COMMODITY_TRIGGERS.items()
                    if any(kw in title_lower for kw in kws)
                ]
                if triggered:
                    events.append({
                        "type": "NEWS_GEO",
                        "headline": title,
                        "triggered_commodities": triggered,
                        "source_ticker": ticker,
                        "priority": "HIGH",
                    })
                    continue

                if ticker not in config.MARKET_INDICATORS and ticker not in config.COMMODITIES:
                    if any(kw in title_lower for kw in _STOCK_NEWS_KEYWORDS):
                        events.append({
                            "type": "NEWS_STOCK",
                            "headline": title,
                            "source_ticker": ticker,
                            "priority": "MEDIUM",
                        })

        if new_hashes:
            portfolio["seen_news"] = {today: list(seen_today | set(new_hashes))}
            save_portfolio(portfolio)

        return events


# ---------- Watch-level event detection ----------

def _get_event_key(event: dict) -> str:
    """Unique key per event (dedup today)."""
    if event["type"] == "WATCH_LEVEL_HIT":
        return f"watch_{event['ticker']}_{event['trigger_price']}"
    return str(event)


def _is_event_already_triggered(event_key: str, portfolio: dict) -> bool:
    triggered = portfolio.get("triggered_events", [])
    today = str(date.today())
    for t in triggered:
        if t.get("key") == event_key and t.get("date") == today:
            return True
    return False


def _mark_events_triggered(events: list[dict]):
    """Mark events as triggered so they don't repeat. Reload-merge under lock."""
    with portfolio_lock:
        fresh = load_portfolio()
        today = str(date.today())
        triggered = [t for t in fresh.get("triggered_events", []) if t.get("date") == today]
        for event in events:
            key = _get_event_key(event)
            if not any(t.get("key") == key for t in triggered):
                triggered.append({
                    "key": key,
                    "date": today,
                    "time": datetime.now().strftime("%H:%M"),
                })
        fresh["triggered_events"] = triggered
        save_portfolio(fresh)


def detect_events() -> list[dict]:
    """Returns NEW watch-level hits (dedup'd today). Skips EXCLUDED_TICKERS."""
    events = []
    portfolio = load_portfolio()
    excluded = set(config.EXCLUDED_TICKERS)
    watch_levels = [w for w in portfolio.get("watch_levels", []) if w["ticker"] not in excluded]

    watch_tickers = [w["ticker"] for w in watch_levels]
    all_tickers = [
        t for t in set(watch_tickers + config.WATCHLIST + config.COMMODITIES)
        if t not in excluded
    ]

    if not all_tickers:
        return events

    market_data = get_market_data(all_tickers)

    for level in watch_levels:
        ticker = level["ticker"]
        data = market_data.get(ticker, {})
        if "error" in data or not data.get("price"):
            continue

        current_price = data["price"]
        trigger_price = level.get("trigger_price")
        level_type = level.get("type", "")
        if not trigger_price:
            continue

        distance_pct = abs(current_price - trigger_price) / trigger_price * 100
        if distance_pct <= config.BREAKOUT_TRIGGER_PERCENT:
            vwap_dev = data.get("vwap_dev_atr")
            extreme = isinstance(vwap_dev, (int, float)) and abs(vwap_dev) >= 3.0
            if extreme:
                logger.warning(
                    "Watch-hit %s @ %.2f DROPPED: VWAP-dev %.2f×ATR (extreme spike, likely exhaustion)",
                    ticker, current_price, vwap_dev,
                )
                continue
            anomaly = isinstance(vwap_dev, (int, float)) and abs(vwap_dev) >= 2.0
            event_note = level.get("note", "")
            if anomaly:
                event_note = (event_note + f" ⚠️ VWAP-dev {vwap_dev:+.2f}×ATR (flash-spike warn)").strip()
            events.append({
                "type": "WATCH_LEVEL_HIT",
                "ticker": ticker,
                "level_type": level_type,
                "trigger_price": trigger_price,
                "current_price": current_price,
                "note": event_note,
                "vwap_dev_atr": vwap_dev,
                "anomaly": anomaly,
                "priority": "HIGH",
            })

    # Filter out events that were already triggered today
    new_events = [e for e in events if not _is_event_already_triggered(_get_event_key(e), portfolio)]
    if new_events:
        _mark_events_triggered(new_events)
    return new_events


def should_analyze_events(events: list[dict]) -> tuple[bool, str]:
    """Decide if events are worth an API call."""
    if not events:
        return False, "No events"

    high_priority = [e for e in events if e.get("priority") == "HIGH"]
    if high_priority:
        return True, f"{len(high_priority)} high-priority event(s)"

    minutes_since = get_minutes_since_last_analysis()
    if minutes_since > 60:
        return True, f"Low-priority events, but {minutes_since:.0f}min since last analysis"
    return False, "Only low-priority events, analyzed recently"


# ---------- Trade state helpers (SL/TP/Trailing) ----------

def _next_take_profit(trade: dict) -> float | None:
    """Return next TP target. Supports scalar or list (gestaffelt)."""
    tp = trade.get("take_profit")
    if tp is None:
        return None
    if isinstance(tp, list):
        return float(tp[0]) if tp else None
    return float(tp)


def _pop_first_take_profit(trade: dict):
    """Consume the first TP in a list; scalar TP → None."""
    tp = trade.get("take_profit")
    if isinstance(tp, list):
        tp.pop(0)
        if not tp:
            trade["take_profit"] = None
    else:
        trade["take_profit"] = None


def _apply_trailing_stop(trade: dict, current_price: float) -> bool:
    """Ratchet stop-loss up based on `trailing_stop_pct`. Never moves stop down."""
    trail_pct = trade.get("trailing_stop_pct")
    if not trail_pct or trail_pct <= 0:
        return False

    candidate = current_price * (1 - trail_pct / 100)
    current_stop = trade.get("stop_loss")
    if current_stop is None or candidate > current_stop:
        trade["stop_loss"] = round(candidate, 2)
        return True
    return False


def _close_trade(trade: dict, exit_price: float, reason: str, portfolio: dict):
    """Move a trade from open_trades to closed_trades with exit metadata.
    Credits cash assuming user executes on TR (SL/TP is mirrored by the broker)."""
    entry = trade.get("entry_price", 0)
    shares = trade.get("shares", 0)
    pnl_eur = (exit_price - entry) * shares if entry and shares else 0
    pnl_pct = ((exit_price - entry) / entry * 100) if entry else 0

    closed = dict(trade)
    closed.update({
        "exit_price": exit_price,
        "exit_reason": reason,
        "exit_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pnl_eur": round(pnl_eur, 2),
        "pnl_pct": round(pnl_pct, 2),
        "status": "closed",
    })
    # Auto-tag: SL hit filled ≥ SL_SLIPPAGE_TAG_PERCENT below nominal SL → execution error
    # (fast-market gap or bad fill, not thesis failure). Lets learning loop surface
    # execution-class dominance even without user /close #tag.
    if reason == "STOP_LOSS" and pnl_pct <= 0:
        sl = trade.get("stop_loss")
        if sl and sl > 0:
            slip_below = (sl - exit_price) / sl * 100
            if slip_below >= config.SL_SLIPPAGE_TAG_PERCENT:
                closed["mistake_tag"] = "slippage"
                closed["mistake_class"] = "execution"
                closed["sl_exit_slippage_pct"] = round(slip_below, 3)
                logger.warning(
                    "Auto-tag slippage: %s exit €%.2f vs SL €%.2f (%.2f%% below)",
                    trade.get("ticker", "?"), exit_price, sl, slip_below,
                )
            else:
                closed["mistake_tag"] = None
                closed["mistake_class"] = "untagged"
        else:
            closed["mistake_tag"] = None
            closed["mistake_class"] = "untagged"
    # Brier score: (p_predicted - outcome)^2. outcome=1 if win, 0 if loss.
    p_win = trade.get("p_win")
    if isinstance(p_win, (int, float)) and 0.0 <= p_win <= 1.0:
        outcome = 1 if pnl_pct > 0 else 0
        closed["brier"] = round((p_win - outcome) ** 2, 4)
        closed["outcome"] = outcome

    portfolio.setdefault("closed_trades", []).append(closed)
    portfolio["cash_eur"] = portfolio.get("cash_eur", 0) + (exit_price * shares)


def check_stop_loss_take_profit() -> list[dict]:
    """Check open trades for stop-loss / take-profit triggers.
    On full exit (SL or final TP), moves trade to closed_trades + frees cash.
    Handles trailing stops and break-even shift after TP1."""
    with portfolio_lock:
        portfolio = load_portfolio()
        open_trades = portfolio.get("open_trades", [])

        if not open_trades:
            return []

        alerts = []
        portfolio_dirty = False
        tickers = [t["ticker"] for t in open_trades]
        market_data = get_market_data(tickers)

        surviving_trades = []

        for trade in open_trades:
            ticker = trade["ticker"]
            data = market_data.get(ticker, {})

            if "error" in data:
                surviving_trades.append(trade)
                continue

            current_price = data.get("price")
            if not current_price:
                surviving_trades.append(trade)
                continue

            entry = trade.get("entry_price", 0)

            if _apply_trailing_stop(trade, current_price):
                portfolio_dirty = True
                alerts.append({
                    "type": "TRAILING_STOP_MOVED",
                    "ticker": ticker,
                    "new_stop": trade["stop_loss"],
                    "current_price": current_price,
                })

            stop_loss = trade.get("stop_loss")
            take_profit = _next_take_profit(trade)

            if stop_loss and current_price <= stop_loss:
                pnl_pct = ((current_price - entry) / entry * 100) if entry else 0
                alerts.append({
                    "type": "STOP_LOSS_HIT",
                    "ticker": ticker,
                    "entry": entry,
                    "stop_loss": stop_loss,
                    "current_price": current_price,
                    "pnl_pct": pnl_pct,
                })
                _close_trade(trade, current_price, "STOP_LOSS", portfolio)
                portfolio_dirty = True
                continue

            if take_profit and current_price >= take_profit:
                pnl_pct = ((current_price - entry) / entry * 100) if entry else 0
                had_more_tps = isinstance(trade.get("take_profit"), list) and len(trade["take_profit"]) > 1

                _pop_first_take_profit(trade)
                portfolio_dirty = True

                alerts.append({
                    "type": "TAKE_PROFIT_HIT",
                    "ticker": ticker,
                    "entry": entry,
                    "take_profit": take_profit,
                    "current_price": current_price,
                    "pnl_pct": pnl_pct,
                    "partial": had_more_tps,
                })

                if had_more_tps:
                    if entry and (trade.get("stop_loss") is None or trade["stop_loss"] < entry):
                        trade["stop_loss"] = entry
                        alerts.append({
                            "type": "BREAK_EVEN_SHIFT",
                            "ticker": ticker,
                            "new_stop": entry,
                        })
                    # Runner-protection: activate trailing if not already set.
                    # Why: after TP1, break-even alone gives runner-gain back on pullback.
                    # 1.5×ATR% trail locks profit while letting trend extend.
                    if not trade.get("trailing_stop_pct"):
                        atr_pct = data.get("atr14_pct")
                        if isinstance(atr_pct, (int, float)) and atr_pct > 0:
                            trail_pct = round(atr_pct * 1.5, 2)
                            trade["trailing_stop_pct"] = trail_pct
                            alerts.append({
                                "type": "TRAILING_ACTIVATED",
                                "ticker": ticker,
                                "trail_pct": trail_pct,
                                "atr_pct": atr_pct,
                            })
                    surviving_trades.append(trade)
                else:
                    _close_trade(trade, current_price, "TAKE_PROFIT", portfolio)
                continue

            if stop_loss and current_price <= stop_loss * 1.01:
                alerts.append({
                    "type": "STOP_LOSS_WARNING",
                    "ticker": ticker,
                    "stop_loss": stop_loss,
                    "current_price": current_price,
                    "distance_pct": ((current_price - stop_loss) / stop_loss * 100),
                })

            surviving_trades.append(trade)

        if portfolio_dirty:
            portfolio["open_trades"] = surviving_trades
            # Recompute DD halt latch — SL/TP hits just changed realized equity.
            maintain_drawdown_state(portfolio)
            save_portfolio(portfolio)

        return alerts


def check_price_alerts() -> list[dict]:
    """Check for significant price movements. Pre-filter: only send to Claude if the
    ticker has an active watch-level OR the move is ≥STRONG_PRICE_ALERT_PERCENT.
    Deduplicates per (direction, ticker) per day."""
    with portfolio_lock:
        portfolio = load_portfolio()
        today = str(date.today())

        triggered = [
            t for t in portfolio.get("triggered_price_alerts", [])
            if t.get("date") == today
        ]
        triggered_keys = {t["key"] for t in triggered}

        alerts = []
        excluded = set(config.EXCLUDED_TICKERS)
        watch_tickers = [
            t for t in config.WATCHLIST + config.COMMODITIES if t not in excluded
        ]
        market_data = get_market_data(watch_tickers)

        watch_level_set = {w["ticker"] for w in portfolio.get("watch_levels", [])}

        for ticker, data in market_data.items():
            if "error" in data or ticker in excluded:
                continue

            change = data.get("change_pct")
            if change is None:
                continue

            direction = None
            if change <= -config.PRICE_DROP_ALERT_PERCENT:
                direction = "DROP"
            elif change >= config.PRICE_RISE_ALERT_PERCENT:
                direction = "RISE"

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
            portfolio["triggered_price_alerts"] = triggered
            save_portfolio(portfolio)

        return alerts
