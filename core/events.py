"""Event detection + trade state transitions:
- News scanning (geopolitical + stock-specific)
- Watch-level hit detection
- Price-alert detection with pre-filter
- Stop-loss / take-profit / trailing-stop checks
"""

import hashlib
import logging
import re
from datetime import datetime, date, timedelta

import yfinance as yf

import config
from core.portfolio import portfolio_lock, load_portfolio, save_portfolio, maintain_drawdown_state
from core.market_data import get_market_data
from core.news_rss import fetch_rss_news
from core.api_usage import get_minutes_since_last_analysis

logger = logging.getLogger(__name__)


# ---------- News event detection ----------
# Precision > Recall: prefer false-negatives over false-positives. Word-boundary
# matching via regex; trailing "*" = compound-stem (matches kw + any word chars).
# Stems used only for domain-specific German compounds (no English-word collision).
# English: explicit inflections; ambiguous shorts (beat/miss/cut/loss) use bigrams.

def _kw_to_pattern_part(kw: str) -> str:
    """Build regex fragment for a keyword. `kw*` → stem (\\bkw\\w*\\b); else exact (\\bkw\\b)."""
    if kw.endswith("*"):
        return r"\b" + re.escape(kw[:-1]) + r"\w*\b"
    return r"\b" + re.escape(kw) + r"\b"


def _build_keyword_pattern(keywords: list[str]) -> re.Pattern:
    return re.compile(
        "(?:" + "|".join(_kw_to_pattern_part(kw) for kw in keywords) + ")",
        re.IGNORECASE,
    )


_STOCK_NEWS_KEYWORDS = [
    # English — earnings/results (bigrams for ambiguous "beat"/"miss"/"cut")
    "earnings", "earnings beat", "earnings miss",
    "revenue beat", "revenue miss",
    "guidance", "raised guidance", "lowered guidance", "cut guidance",
    "raised forecast", "lowered forecast", "cut forecast",
    "profit warning",
    # English — analyst actions
    "upgrade", "upgrades", "upgraded",
    "downgrade", "downgrades", "downgraded",
    "outperform", "outperforms", "outperformed",
    "underperform", "underperforms", "underperformed",
    "raised target", "raises target", "lowered target", "lowers target",
    "price target", "cut target", "cuts target",
    "initiated coverage", "initiates coverage",
    "buy rating", "sell rating", "hold rating",
    "overweight", "underweight",
    # English — M&A
    "acquisition", "acquisitions",
    "merger", "mergers",
    "takeover", "takeovers",
    "buyout", "buyouts",
    # English — regulatory
    "fda approval", "fda rejection", "fda warning",
    "drug approval", "drug rejection",
    "recall", "recalls", "recalled",
    # English — leadership/legal
    "ceo", "cfo",
    "resign", "resigns", "resigned", "resignation",
    "investigation", "investigations", "investigated",
    "lawsuit", "lawsuits",
    "fraud", "fraudulent",
    # English — restructuring
    "layoff", "layoffs",
    "restructuring", "restructured", "restructure",
    "bankruptcy", "bankrupt",
    "loan default", "debt default", "default risk",
    # English — capital actions
    "dividend", "dividends",
    "buyback", "buybacks",
    "stock split", "share split", "reverse split",
    # German — compound stems (precision-safe, domain-specific)
    "übernahm*",       # übernahme, übernahmeangebot, übernahmeversuch
    "quartalszahl*", "quartalsergebnis*",
    "gewinnwarn*",     # gewinnwarnung, gewinnwarnungen
    "gewinneinbruch*", "gewinnsprung*",
    "prognose*",       # prognose, prognosen, prognoseanhebung, prognosesenkung
    "insolvenz*",      # insolvenz, insolvenzantrag, insolvenzverfahren
    "stellenabbau*",
    "rücktritt*",
    "kursziel*",       # kursziel, kursziele, kurszielanhebung
    "hochstuf*", "hochgestuft",
    "herabstuf*", "herabgestuft",
    "abstuf*",
    "skandal*",
    "ermittlung*",
    "betrugs*",        # betrugsfall, betrugsverdacht (bare "betrug" too generic verb form)
    "dividend*",       # dividende, dividenden, dividendenkürzung
    "aktienrückkauf*",
    "umsatzsprung*", "umsatzeinbruch*",
    # German — exact + bigrams for analyst recs (bare kaufen/verkaufen/halten dropped: too generic)
    "fusion", "fusionen",
    "ausblick",
    "kaufempfehl*", "verkaufsempfehl*", "halteempfehl*",
    "auf kaufen", "auf verkaufen", "auf halten",
]

# Rating-change keywords: subset that signals fresh analyst action.
# These bump priority to HIGH and tag subtype="rating_change" so Claude
# weights them above stale consensus from market_data snapshot.
_RATING_CHANGE_KEYWORDS = [
    "upgrade", "upgrades", "upgraded",
    "downgrade", "downgrades", "downgraded",
    "outperform", "outperforms", "outperformed",
    "underperform", "underperforms", "underperformed",
    "raised target", "raises target", "lowered target", "lowers target",
    "price target", "cut target", "cuts target",
    "initiated coverage", "initiates coverage",
    "buy rating", "sell rating", "hold rating",
    "overweight", "underweight",
    "kursziel*",
    "hochstuf*", "hochgestuft",
    "herabstuf*", "herabgestuft",
    "abstuf*",
    "kaufempfehl*", "verkaufsempfehl*", "halteempfehl*",
    "auf kaufen", "auf verkaufen", "auf halten",
]

_STOCK_NEWS_PATTERN = _build_keyword_pattern(_STOCK_NEWS_KEYWORDS)
_RATING_CHANGE_PATTERN = _build_keyword_pattern(_RATING_CHANGE_KEYWORDS)

# Per-trigger pattern (need to know which commodity matched).
_COMMODITY_TRIGGER_PATTERNS = {
    comm: _build_keyword_pattern(kws)
    for comm, kws in config.COMMODITY_TRIGGERS.items()
}


def _ticker_in_title(title: str, ticker: str, name: str | None) -> bool:
    """True if title plausibly mentions the ticker — bare symbol or first significant
    company-name token. Filters RSS junk where Google News returned tangential results
    (e.g. broader market commentary that happens to match a keyword).
    Does NOT disambiguate symbol-collision edge cases (e.g. ticker "RWE" vs football
    club "RWE Essen" — both contain bare "RWE"). Upstream finance-keyword query is
    the primary defense there.
    """
    title_lower = title.lower()
    bare = ticker.split(".")[0].lower()
    if re.search(r"\b" + re.escape(bare) + r"\b", title_lower):
        return True
    if name:
        name_lower = name.lower()
        if name_lower in title_lower:
            return True
        # Extract word tokens (strip punctuation: "Tesla, Inc." → ["tesla", "inc"]).
        # Match on ANY ≥4-char token (Bug 2026-05-02: premature break only checked
        # the first token — "International Business Machines" matched on bare
        # "international" → false-positives on unrelated articles).
        for tok in re.findall(r"\w+", name_lower):
            if len(tok) >= 4 and re.search(r"\b" + re.escape(tok) + r"\b", title_lower):
                return True
    return False


def _classify_headline(
    title: str,
    ticker: str,
    is_open_position: bool,
    name: str | None = None,
) -> dict | None:
    """Match headline against geo/stock/rating keywords. Returns event dict or None."""
    triggered = [
        comm for comm, pat in _COMMODITY_TRIGGER_PATTERNS.items()
        if pat.search(title)
    ]
    if triggered:
        return {
            "type": "NEWS_GEO",
            "headline": title,
            "triggered_commodities": triggered,
            "source_ticker": ticker,
            "priority": "HIGH",
        }

    if ticker in config.MARKET_INDICATORS or ticker in config.COMMODITIES:
        return None

    # Ticker-relevance gate: drop headlines that don't mention the ticker/company.
    # RSS fuzzy-matches and may return tangential results; classifier would otherwise
    # match keywords on unrelated stories.
    if not _ticker_in_title(title, ticker, name):
        return None

    is_rating_change = bool(_RATING_CHANGE_PATTERN.search(title))
    has_stock_kw = bool(_STOCK_NEWS_PATTERN.search(title))

    if not (is_rating_change or has_stock_kw):
        return None

    # Rating change on an open position = HIGH (act fast). Otherwise MEDIUM.
    # Open-position non-rating news also HIGH so Claude reviews exit risk first.
    if is_rating_change:
        priority = "HIGH" if is_open_position else "MEDIUM"
        subtype = "rating_change"
    else:
        priority = "HIGH" if is_open_position else "MEDIUM"
        subtype = "stock_news"

    return {
        "type": "NEWS_STOCK",
        "subtype": subtype,
        "headline": title,
        "source_ticker": ticker,
        "priority": priority,
    }


def check_news_events() -> list[dict]:
    """Scan yfinance + Google News RSS for all watchlist/open/commodity tickers.
    Returns new actionable events not seen before today.
    Persists seen article hashes in portfolio.json (under lock)."""
    with portfolio_lock:
        portfolio = load_portfolio()
        today = str(date.today())
        seen_today = set(portfolio.get("seen_news", {}).get(today, []))

        open_tickers = {t["ticker"] for t in portfolio.get("open_trades", [])}
        scan_tickers = list(dict.fromkeys(
            list(config.MARKET_INDICATORS) + list(open_tickers) + config.WATCHLIST + config.COMMODITIES
        ))

        # RSS scan only for stock tickers (open + watchlist). Skip indices/commodities —
        # yfinance covers those, and RSS query "SPY" or "GC=F" returns junk.
        rss_tickers = set(open_tickers) | set(config.WATCHLIST)

        # Pull company names from market_data cache to disambiguate ticker queries
        # (e.g. "RWE" alone matches Rot-Weiss Essen football headlines).
        names_by_ticker = {}
        if rss_tickers:
            try:
                snapshots = get_market_data(list(rss_tickers))
                names_by_ticker = {
                    t: (s or {}).get("name") for t, s in snapshots.items()
                }
            except Exception as e:
                logger.warning("market_data lookup for RSS names failed: %s", e)

        events = []
        new_hashes = []

        for ticker in scan_tickers:
            is_open = ticker in open_tickers

            # Source 1: yfinance (US/EN-biased, fast)
            titles: list[str] = []
            try:
                items = yf.Ticker(ticker).news or []
                titles.extend(it.get("title", "") for it in items)
            except Exception:
                pass

            # Source 2: Google News RSS (multilingual, catches German sources)
            if ticker in rss_tickers:
                try:
                    rss_items = fetch_rss_news(
                        ticker,
                        name=names_by_ticker.get(ticker),
                        max_age_hours=24,
                    )
                    titles.extend(it["title"] for it in rss_items)
                except Exception as e:
                    logger.warning("RSS fetch failed for %s: %s", ticker, e)

            for title in titles:
                if not title:
                    continue
                h = hashlib.md5(title.lower().encode()).hexdigest()[:16]
                if h in seen_today:
                    continue
                new_hashes.append(h)
                seen_today.add(h)  # dedup within same cycle (same story across sources)

                event = _classify_headline(
                    title, ticker,
                    is_open_position=is_open,
                    name=names_by_ticker.get(ticker),
                )
                if event:
                    events.append(event)

        if new_hashes:
            # Keep yesterday too so a news-cycle that spans midnight doesn't re-fire
            # 24h-old articles still in the RSS feed (Bug 2026-05-02: prior code
            # overwrote seen_news with {today: ...} → loss on next-day rollover).
            yesterday = str(date.today() - timedelta(days=1))
            existing = portfolio.get("seen_news", {}) or {}
            portfolio["seen_news"] = {
                today: list(seen_today),
                yesterday: list(existing.get(yesterday, [])),
            }
            save_portfolio(portfolio)

        return events


# ---------- Watch-level event detection ----------

def _get_event_key(event: dict) -> str:
    """Unique key per event (dedup today)."""
    if event["type"] == "WATCH_LEVEL_HIT":
        return f"watch_{event['ticker']}_{event['trigger_price']}"
    if event["type"] == "WATCH_INVALIDATED":
        return f"watch_invalid_{event['ticker']}_{event.get('invalidate_below')}"
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
    """Returns NEW watch-level hits (dedup'd today). Skips EXCLUDED_TICKERS.

    Compound-condition gate (Sonnet-Thesis-Pattern, 2026-04-27):
    - `valid_until` past   → silent expire, drop from watch_levels
    - `invalidate_below`   → fire WATCH_INVALIDATED event + drop watch
    - `confirm_close_above`→ trigger only if current price ≥ threshold (anti-tag-and-dip)
    - `min_volume_ratio`   → trigger only if vol_ratio ≥ threshold (anti-fake-breakout)
    - distance_pct ≤ BREAKOUT_TRIGGER_PERCENT (existing proximity check)

    All conditions AND-combined. Designed so Haiku-Event-Mode does no re-reasoning —
    Sonnet's morning thesis-conditions are the only gate.
    """
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

    today = date.today()
    expired_indices: list[int] = []
    invalidated_indices: list[int] = []

    for idx, level in enumerate(watch_levels):
        ticker = level["ticker"]
        data = market_data.get(ticker, {})
        if "error" in data or not data.get("price"):
            continue

        current_price = data["price"]
        trigger_price = level.get("trigger_price")
        level_type = level.get("type", "")
        if not trigger_price:
            continue

        valid_until = level.get("valid_until")
        if valid_until:
            try:
                vu = datetime.strptime(valid_until, "%Y-%m-%d").date()
                if today > vu:
                    logger.info("Watch %s expired (valid_until=%s)", ticker, valid_until)
                    expired_indices.append(idx)
                    continue
            except ValueError:
                logger.warning("Watch %s has invalid valid_until=%r", ticker, valid_until)

        invalidate_below = level.get("invalidate_below")
        if isinstance(invalidate_below, (int, float)) and invalidate_below > 0:
            if current_price < invalidate_below:
                logger.warning(
                    "Watch %s INVALIDATED: price %.2f < invalidate_below %.2f",
                    ticker, current_price, invalidate_below,
                )
                events.append({
                    "type": "WATCH_INVALIDATED",
                    "ticker": ticker,
                    "level_type": level_type,
                    "trigger_price": trigger_price,
                    "current_price": current_price,
                    "invalidate_below": invalidate_below,
                    "thesis": level.get("thesis", ""),
                    "note": f"These gebrochen: Preis {current_price:.2f} < Invalid-Schwelle {invalidate_below:.2f}",
                    "priority": "HIGH",
                })
                invalidated_indices.append(idx)
                continue

        distance_pct = abs(current_price - trigger_price) / trigger_price * 100
        if distance_pct <= config.BREAKOUT_TRIGGER_PERCENT:
            confirm_close_above = level.get("confirm_close_above")
            if isinstance(confirm_close_above, (int, float)) and confirm_close_above > 0:
                if current_price < confirm_close_above:
                    logger.info(
                        "Watch %s @%.2f not confirmed: price %.2f < confirm_close_above %.2f",
                        ticker, trigger_price, current_price, confirm_close_above,
                    )
                    continue

            min_vol = level.get("min_volume_ratio")
            if isinstance(min_vol, (int, float)) and min_vol > 0:
                vr = data.get("volume_ratio")
                if not isinstance(vr, (int, float)) or vr < min_vol:
                    logger.info(
                        "Watch %s @%.2f vol-gate fail: vol_ratio %s < min %.2f",
                        ticker, trigger_price, vr, min_vol,
                    )
                    continue

            # Direction-confirm gate: a `resistance_reject` is only meaningful if
            # price is actually back below the trigger; a `support_bounce` only if
            # back above. Without this, bot fires on the natural tag-and-continue
            # of a breakout (Bug 2026-04-27: RWE @60.70 → tag → continue → bot saw
            # 15-min snapshot of dip and recommended EXIT).
            # Buffer: 0.25×ATR when available, else 0.3% absolute.
            atr_pct = data.get("atr14_pct")
            if isinstance(atr_pct, (int, float)) and atr_pct > 0:
                buf_pct = 0.25 * atr_pct
            else:
                buf_pct = 0.3
            buf_abs = trigger_price * buf_pct / 100
            if level_type == "resistance_reject" and current_price > trigger_price - buf_abs:
                logger.info(
                    "Watch %s resistance_reject @%.2f not confirmed: price %.2f "
                    "(needs ≤ %.2f for reject)",
                    ticker, trigger_price, current_price, trigger_price - buf_abs,
                )
                continue
            if level_type == "support_bounce" and current_price < trigger_price + buf_abs:
                logger.info(
                    "Watch %s support_bounce @%.2f not confirmed: price %.2f "
                    "(needs ≥ %.2f for bounce)",
                    ticker, trigger_price, current_price, trigger_price + buf_abs,
                )
                continue
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
                "thesis": level.get("thesis", ""),
                "invalidate_below": level.get("invalidate_below"),
                "vwap_dev_atr": vwap_dev,
                "anomaly": anomaly,
                "priority": "HIGH",
            })

    # Persist expiry + invalidation removals (drop levels whose conditions are gone).
    drop = set(expired_indices) | set(invalidated_indices)
    if drop:
        with portfolio_lock:
            fresh = load_portfolio()
            current = fresh.get("watch_levels", [])
            keep_keys = {
                (watch_levels[i].get("ticker"), watch_levels[i].get("trigger_price"), watch_levels[i].get("type"))
                for i in range(len(watch_levels)) if i not in drop
            }
            kept = [
                lvl for lvl in current
                if (lvl.get("ticker"), lvl.get("trigger_price"), lvl.get("type")) in keep_keys
            ]
            if len(kept) != len(current):
                fresh["watch_levels"] = kept
                save_portfolio(fresh)
                logger.info(
                    "Watch-levels pruned: %d → %d (expired=%d, invalidated=%d)",
                    len(current), len(kept), len(expired_indices), len(invalidated_indices),
                )

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


def _close_partial(trade: dict, shares_to_sell: float, exit_price: float, reason: str, portfolio: dict) -> dict:
    """Sell `shares_to_sell` shares of an open trade at exit_price. Reduces trade.shares
    on remainder. Records the partial sell as its own closed_trades entry with
    `partial=True`. Frees cash. Does NOT touch SL/TP — caller handles (e.g. BE-shift).
    Returns the closed-partial dict (for alert metadata)."""
    entry = float(trade.get("entry_price", 0) or 0)
    pnl_eur = (exit_price - entry) * shares_to_sell if entry else 0.0
    pnl_pct = ((exit_price - entry) / entry * 100) if entry else 0.0

    partial = dict(trade)
    partial.update({
        "shares": round(shares_to_sell, 4),
        "exit_price": exit_price,
        "exit_reason": reason,
        "exit_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pnl_eur": round(pnl_eur, 2),
        "pnl_pct": round(pnl_pct, 2),
        "status": "closed_partial",
        "partial": True,
    })
    # Brier on partial: only score if this is the FIRST close (TP1 hit). Re-scoring on
    # later partials would double-count. Mark via 'partial_seq' counter.
    seq = (trade.get("partial_seq") or 0) + 1
    trade["partial_seq"] = seq
    p_win = trade.get("p_win")
    if seq == 1 and isinstance(p_win, (int, float)) and 0.0 <= p_win <= 1.0:
        outcome = 1 if pnl_pct > 0 else 0
        partial["brier"] = round((p_win - outcome) ** 2, 4)
        partial["outcome"] = outcome

    portfolio.setdefault("closed_trades", []).append(partial)
    portfolio["cash_eur"] = portfolio.get("cash_eur", 0) + (exit_price * shares_to_sell)

    remaining = round(float(trade.get("shares", 0) or 0) - shares_to_sell, 4)
    trade["shares"] = max(remaining, 0.0)
    trade["size_eur"] = round(trade["shares"] * entry, 2) if entry else 0.0
    return partial


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

    # Alpha vs Beta attribution (same period as trade hold-window).
    try:
        from core.market_data import get_period_return
        spy_ret = get_period_return("SPY5.DE", trade.get("entry_date", ""), closed["exit_date"])
        if spy_ret is not None:
            closed["spy_return_pct"] = spy_ret
            closed["alpha_pct"] = round(pnl_pct - spy_ret, 2)
    except Exception:
        logger.exception("SPY-attribution failed for %s", trade.get("ticker", "?"))

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

            # Time-Stop: stale trade auto-close. Frees heat for fresh setups.
            # Skip if trade already had a partial TP-hit (those locked in profit, let runner work).
            time_stop_days = trade.get("time_stop_days") or config.TIME_STOP_DAYS
            entry_date_str = trade.get("entry_date") or ""
            partial_count = trade.get("partial_seq") or 0
            if time_stop_days and entry_date_str and partial_count == 0:
                try:
                    entry_dt = datetime.strptime(entry_date_str, "%Y-%m-%d %H:%M")
                    held_days = (datetime.now() - entry_dt).days
                    if held_days >= time_stop_days:
                        pnl_pct = ((current_price - entry) / entry * 100) if entry else 0
                        alerts.append({
                            "type": "TIME_STOP_HIT",
                            "ticker": ticker,
                            "entry": entry,
                            "current_price": current_price,
                            "held_days": held_days,
                            "pnl_pct": pnl_pct,
                        })
                        _close_trade(trade, current_price, "TIME_STOP", portfolio)
                        portfolio_dirty = True
                        continue
                except ValueError:
                    pass

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

                if had_more_tps:
                    # Partial close: lock PARTIAL_TP_FRACTION × shares at TP1.
                    # Remainder runs with BE-SL + trailing. Locks ≥0.5R win even if runner stops out.
                    shares_total = float(trade.get("shares", 0) or 0)
                    shares_to_sell = round(shares_total * config.PARTIAL_TP_FRACTION, 4)
                    if shares_to_sell > 0 and shares_to_sell < shares_total:
                        _close_partial(trade, shares_to_sell, current_price, "TAKE_PROFIT_PARTIAL", portfolio)
                        alerts.append({
                            "type": "PARTIAL_TP_HIT",
                            "ticker": ticker,
                            "entry": entry,
                            "take_profit": take_profit,
                            "current_price": current_price,
                            "pnl_pct": pnl_pct,
                            "shares_sold": shares_to_sell,
                            "shares_remaining": trade["shares"],
                        })
                    else:
                        # Edge: fractional share too small to split — treat as full TP-target reached.
                        alerts.append({
                            "type": "TAKE_PROFIT_HIT",
                            "ticker": ticker,
                            "entry": entry,
                            "take_profit": take_profit,
                            "current_price": current_price,
                            "pnl_pct": pnl_pct,
                            "partial": False,
                        })
                    _pop_first_take_profit(trade)
                    portfolio_dirty = True

                    if entry and (trade.get("stop_loss") is None or trade["stop_loss"] < entry):
                        trade["stop_loss"] = entry
                        alerts.append({
                            "type": "BREAK_EVEN_SHIFT",
                            "ticker": ticker,
                            "new_stop": entry,
                        })
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
                    if trade.get("shares", 0) > 0:
                        surviving_trades.append(trade)
                else:
                    # Final TP — close full remainder.
                    alerts.append({
                        "type": "TAKE_PROFIT_HIT",
                        "ticker": ticker,
                        "entry": entry,
                        "take_profit": take_profit,
                        "current_price": current_price,
                        "pnl_pct": pnl_pct,
                        "partial": False,
                    })
                    _pop_first_take_profit(trade)
                    _close_trade(trade, current_price, "TAKE_PROFIT", portfolio)
                    portfolio_dirty = True
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
