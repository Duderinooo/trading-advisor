"""Watch-level event detection + prefilter + routing decision.

detect_events():       new watch-level hits per cycle (TTL-deduped via dedup.py)
prefilter_entry_events: drop hits a deterministic analyzer gate would reject
                       anyway (saves a doomed Haiku call)
should_analyze_events: routing decision — fire analyzer or not
"""

import logging
from datetime import date, datetime

import config
from core.events.dedup import (
    get_event_key, in_no_entry_window, is_event_already_triggered,
    mark_events_triggered,
)
from core.events.types import EventType
from core.data.market_data import get_market_data, market_regime
from core.llm.telemetry.api_usage import get_minutes_since_last_analysis
from core.portfolio import (
    portfolio_lock, load_portfolio, save_portfolio,
    active_entry_gate_cooldowns, compute_confluence, compute_sector_exposure,
    exit_suppressed_tickers, risk_halt_status,
)


logger = logging.getLogger(__name__)


def prefilter_entry_events(
    events: list[dict], portfolio: dict, market_data: dict, regime: str,
) -> list[dict]:
    """Drop WATCH_LEVEL_HIT events whose entry a deterministic analyzer gate
    guarantees to reject — skips a doomed Haiku event-call.

    Strict-lenient: event dropped only when block holds across every choice
    Claude could make. Gates with Claude-controlled overrides (earnings_drift,
    mean-reversion RS, breakout volume) are NOT mirrored here — those stay soft
    so a legit override is never lost.

    Mirrors analyzer gates: risk-halt, regime, sector-cap, wk_trend, confluence.
    WATCH_INVALIDATED events pass through untouched (invalidation is not entry).
    """
    hits = [e for e in events if e.get("type") == EventType.WATCH_LEVEL_HIT]
    if not hits:
        return events
    others = [e for e in events if e.get("type") != EventType.WATCH_LEVEL_HIT]

    # ---- Portfolio-wide blocks: one fails → every pending entry is doomed. ----
    halt = risk_halt_status(portfolio)
    if halt["halt"]:
        logger.info(
            "prefilter: %d watch-hit(s) dropped — risk-halt active (%s)",
            len(hits), "; ".join(halt["reasons"]),
        )
        return others
    if (config.RISK_OFF_BLOCKS_LONGS and isinstance(regime, str)
            and regime.startswith("RISK_OFF")):
        logger.info(
            "prefilter: %d watch-hit(s) dropped — regime %s blocks longs",
            len(hits), regime,
        )
        return others

    # ---- Per-ticker deterministic blocks. ----
    sector_counts = {
        sec: len(tks) for sec, tks in compute_sector_exposure(portfolio).items()
    }
    # mean-reversion confluence relaxation (-2) baked into the floor → most
    # lenient threshold any setup family could face. Below it = guaranteed block.
    conf_floor = config.MIN_CONFLUENCE_SCORE - 2

    kept: list[dict] = []
    for e in hits:
        t = e["ticker"]
        snap = market_data.get(t, {})
        if snap.get("wk_trend") == "DOWN":
            logger.info("prefilter: %s watch-hit dropped — wk_trend DOWN", t)
            continue
        sec = config.SECTOR_MAP.get(t, "other")
        if sector_counts.get(sec, 0) >= config.MAX_POSITIONS_PER_SECTOR:
            logger.info(
                "prefilter: %s watch-hit dropped — sector '%s' at cap %d",
                t, sec, config.MAX_POSITIONS_PER_SECTOR,
            )
            continue
        # confluence floor — skipped when regime is UNKNOWN (score would be
        # understated by missing regime_risk_on point → false drop).
        if (regime != "UNKNOWN" and snap and not snap.get("error")
                and snap.get("price")):
            score = compute_confluence(snap, regime)["score"]
            if score < conf_floor:
                logger.info(
                    "prefilter: %s watch-hit dropped — confluence %d < %d floor",
                    t, score, conf_floor,
                )
                continue
        kept.append(e)

    dropped = len(hits) - len(kept)
    if dropped:
        logger.info(
            "prefilter: %d/%d watch-hit(s) deferred by deterministic gates",
            dropped, len(hits),
        )
    return others + kept


def detect_events() -> list[dict]:
    """Return NEW watch-level hits (dedup'd today). Skips EXCLUDED_TICKERS.

    Compound-condition gate (Sonnet-Thesis-Pattern, 2026-04-27):
    - `valid_until` past   → silent expire, drop from watch_levels
    - `invalidate_below`   → fire WATCH_INVALIDATED event + drop watch
    - `confirm_close_above`→ trigger only if current price ≥ threshold
    - `min_volume_ratio`   → trigger only if vol_ratio ≥ threshold
    - distance_pct ≤ BREAKOUT_TRIGGER_PERCENT (existing proximity check)

    All conditions AND-combined. Haiku-Event-Mode does no re-reasoning —
    Sonnet's morning thesis-conditions are the only gate.
    """
    events: list[dict] = []
    portfolio = load_portfolio()
    excluded = set(config.EXCLUDED_TICKERS)
    # Open-position tickers: distinguish defense-watches (Haiku-Call needed) from
    # entry-search-watches (Telegram-notify only — Sonnet's morning Limit-Buy plan
    # is already the action signal). Architecture refactor 2026-05-21.
    open_pos_tickers = {
        (t.get("ticker") or "").upper()
        for t in portfolio.get("open_trades", [])
    }
    suppressed = {t.upper() for t in exit_suppressed_tickers(portfolio)}
    # Entry-gate-cooldown: ticker just failed edge/RS gate. Watch-hit would only
    # burn a Haiku call for a rec the hard gate rejects again (incident
    # 2026-05-18: BAS.DE 2× edge-block 61min apart). After cooldown elapses,
    # detect_events sees the ticker again — setup deferred, not discarded.
    entry_cd = set(active_entry_gate_cooldowns(portfolio).keys())
    skip = suppressed | entry_cd
    watch_levels = [
        w for w in portfolio.get("watch_levels", [])
        if w.get("ticker")
        and w["ticker"] not in excluded
        and (w.get("ticker") or "").upper() not in skip
    ]

    watch_tickers = [w["ticker"] for w in watch_levels]
    all_tickers = [
        t for t in set(watch_tickers + config.WATCHLIST + config.COMMODITIES)
        if t not in excluded and t.upper() not in skip
    ]
    if suppressed:
        logger.info("detect_events: %d ticker(s) exit-suppressed: %s",
                    len(suppressed), ", ".join(sorted(suppressed)))
    if entry_cd:
        logger.info("detect_events: %d ticker(s) entry-gate-cooldown: %s",
                    len(entry_cd), ", ".join(sorted(entry_cd)))

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

        # ---- Expiry check ----
        valid_until = level.get("valid_until")
        if valid_until:
            try:
                vu = datetime.strptime(valid_until, "%Y-%m-%d").date()
                if today > vu:
                    logger.info("Watch %s expired (valid_until=%s)", ticker, valid_until)
                    expired_indices.append(idx)
                    continue
            except ValueError:
                # Corrupt valid_until — treat as expired (broken thesis-bound
                # shouldn't trigger entries).
                logger.warning(
                    "Watch %s expired (invalid valid_until=%r — treating as expired)",
                    ticker, valid_until,
                )
                expired_indices.append(idx)
                continue

        # ---- Invalidation check ----
        invalidate_below = level.get("invalidate_below")
        if isinstance(invalidate_below, (int, float)) and invalidate_below > 0:
            if current_price < invalidate_below:
                logger.warning(
                    "Watch %s INVALIDATED: price %.2f < invalidate_below %.2f",
                    ticker, current_price, invalidate_below,
                )
                events.append({
                    "type": EventType.WATCH_INVALIDATED,
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

        # ---- Proximity check: zone-mode vs legacy line-mode ----
        # Zone-mode (explicit zone_low+zone_high) skips line-confirm gates —
        # being in the zone IS the trigger. Fake-signal protections (min_vol,
        # VWAP-anomaly) still apply.
        zone_low = level.get("zone_low")
        zone_high = level.get("zone_high")
        zone_mode = (
            isinstance(zone_low, (int, float))
            and isinstance(zone_high, (int, float))
            and zone_low < zone_high
        )
        if zone_mode:
            in_proximity = zone_low <= current_price <= zone_high
        else:
            distance_pct = abs(current_price - trigger_price) / trigger_price * 100
            in_proximity = distance_pct <= config.BREAKOUT_TRIGGER_PERCENT

        if not in_proximity:
            continue

        # ---- Line-mode confirm gates (skipped in zone-mode) ----
        if not zone_mode:
            level_type_lower = level_type.lower() if isinstance(level_type, str) else ""
            is_long_breakout = level_type_lower in ("breakout_long", "breakout_resistance", "breakout")
            if is_long_breakout and current_price < trigger_price:
                # Direction-aware proximity: abs() is symmetric, but for breakout_long
                # without confirm_close_above, bot would fire on BELOW trigger too.
                logger.debug(
                    "Watch %s @%.2f long-breakout proximity-only fail: price %.2f < trigger",
                    ticker, trigger_price, current_price,
                )
                continue
            confirm_close_above = level.get("confirm_close_above")
            if isinstance(confirm_close_above, (int, float)) and confirm_close_above > 0:
                # Tick + spread can leave price 1-2ct under Sonnet's confirm threshold
                # all day even though structural breakout already happened.
                tolerance = confirm_close_above * config.CONFIRM_CLOSE_TOLERANCE_PCT / 100
                if current_price < (confirm_close_above - tolerance):
                    logger.info(
                        "Watch %s @%.2f not confirmed: price %.2f < confirm_close_above %.2f (slack %.2f)",
                        ticker, trigger_price, current_price, confirm_close_above, tolerance,
                    )
                    continue

        # ---- Volume gate (zone + line modes) ----
        min_vol = level.get("min_volume_ratio")
        if isinstance(min_vol, (int, float)) and min_vol > 0:
            vr = data.get("volume_ratio")
            if not isinstance(vr, (int, float)) or vr < min_vol:
                logger.info(
                    "Watch %s @%.2f vol-gate fail: vol_ratio %s < min %.2f",
                    ticker, trigger_price, vr, min_vol,
                )
                continue

        # ---- Direction-confirm buffer (line-mode only) ----
        if not zone_mode:
            # resistance_reject only meaningful if price actually back BELOW trigger;
            # support_bounce only if back ABOVE. Bug 2026-04-27: RWE breakout @60.70
            # tag-and-continue read as exit by bot.
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

        # ---- VWAP-anomaly gate ----
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

        # ---- Emit event ----
        # Watch-Hit on ticker WITHOUT open position = Entry-Search-Hit. Sonnet's
        # morning Limit-Buy-Rec is already the action signal — Haiku does NOT
        # generate new entry recs from these. Fire-as-NOTIFY (Telegram only, no
        # Claude call). Watch-Hit WITH open position = Defense-Hit
        # (thesis-degradation, resistance-reject) → HIGH-priority Haiku defender.
        has_open_pos = ticker.upper() in open_pos_tickers
        events.append({
            "type": EventType.WATCH_LEVEL_HIT if has_open_pos else EventType.WATCH_LEVEL_NOTIFY,
            "ticker": ticker,
            "level_type": level_type,
            "trigger_price": trigger_price,
            "current_price": current_price,
            "note": event_note,
            "thesis": level.get("thesis", ""),
            "invalidate_below": level.get("invalidate_below"),
            "vwap_dev_atr": vwap_dev,
            "anomaly": anomaly,
            "priority": "HIGH" if has_open_pos else "MEDIUM",
            "source": level.get("source", ""),
        })

    # ---- Persist expiry + invalidation removals ----
    drop = set(expired_indices) | set(invalidated_indices)
    if drop:
        # Richer identity to avoid collision when two watch_levels share
        # (ticker, trigger_price, type) but differ on conditions.
        def _ident(lvl: dict) -> tuple:
            return (
                lvl.get("ticker"),
                lvl.get("trigger_price"),
                lvl.get("type"),
                lvl.get("thesis"),
                lvl.get("invalidate_below"),
                lvl.get("confirm_close_above"),
                lvl.get("valid_until"),
            )
        drop_idents = {_ident(watch_levels[i]) for i in drop}
        with portfolio_lock:
            fresh = load_portfolio()
            current = fresh.get("watch_levels", [])
            # Only drop FIRST occurrence per identity in case of duplicates.
            remaining = dict.fromkeys(drop_idents, False)
            kept = []
            for lvl in current:
                ident = _ident(lvl)
                if ident in remaining and not remaining[ident]:
                    remaining[ident] = True
                    continue
                kept.append(lvl)
            if len(kept) != len(current):
                fresh["watch_levels"] = kept
                save_portfolio(fresh)
                logger.info(
                    "Watch-levels pruned: %d → %d (expired=%d, invalidated=%d)",
                    len(current), len(kept), len(expired_indices), len(invalidated_indices),
                )

    # ---- No-entry-window deferral (line-mode only — invalidations exempt) ----
    # Watch-hit inside NO_ENTRY_WINDOW would only burn a Haiku call for a rec
    # analyzer's gate #3 rejects post-Claude. Dropped BEFORE the dedup-mark so
    # it re-fires normally once window passes. Incident 2026-05-18: CON.DE 09:05
    # watch-hit burned Haiku call for 09:00–09:10 auction block.
    if in_no_entry_window(datetime.now()):
        deferred = [e for e in events if e.get("type") == EventType.WATCH_LEVEL_HIT]
        if deferred:
            events = [e for e in events if e.get("type") != EventType.WATCH_LEVEL_HIT]
            logger.info(
                "detect_events: %d watch-hit(s) deferred — no-entry window: %s",
                len(deferred), ", ".join(e["ticker"] for e in deferred),
            )

    # ---- Pre-Claude entry-gate filter ----
    if any(e.get("type") == EventType.WATCH_LEVEL_HIT for e in events):
        try:
            _regime = market_regime(get_market_data(list(config.MARKET_INDICATORS)))
        except Exception:
            logger.warning("prefilter: regime fetch failed — regime gate skipped")
            _regime = "UNKNOWN"
        events = prefilter_entry_events(events, portfolio, market_data, _regime)

    # ---- TTL-dedup filter + persist ----
    new_events = [e for e in events if not is_event_already_triggered(get_event_key(e), portfolio)]
    if new_events:
        mark_events_triggered(new_events)
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
