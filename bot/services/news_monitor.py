"""News-monitor service: geo + stock news → optional Claude dispatch.

Carries module-level failure counter to alert after 3× consecutive failures.
"""

import logging
from datetime import datetime, timedelta

import config
from core import (
    analyze_portfolio, check_news_events,
    load_portfolio, save_portfolio, portfolio_lock,
    kill_switch_active,
)
from notifier import send_alert

from runtime.scheduler import is_market_hours, is_weekend_news_window
from runtime.state import AppState


logger = logging.getLogger("trading_advisor.news_monitor")


def run_news_check(state: AppState) -> None:
    """Scan for actionable headlines. Geo bypasses cooldown; stock respects."""
    if not (is_market_hours() or is_weekend_news_window()):
        return
    if kill_switch_active(load_portfolio()):
        logger.debug("News check skipped: kill-switch active")
        return

    try:
        news_events = check_news_events()
        if not news_events:
            return

        geo = [e for e in news_events if e["type"] == "NEWS_GEO"]
        stocks = [e for e in news_events if e["type"] == "NEWS_STOCK"]

        # Geo dedup: 6h window per commodity-set (state/dedup.json).
        from core.portfolio.dedup_store import load_dedup, save_dedup
        _dedup = load_dedup()
        _geo_seen = _dedup.get("geo_news_fired", {}) or {}
        _now_iso = datetime.now().isoformat()
        _cutoff = (datetime.now() - timedelta(hours=6)).isoformat()
        _dirty = False

        for event in geo:
            comms = ", ".join(event["triggered_commodities"])
            headline = event["headline"]
            comm_key = "+".join(sorted(event["triggered_commodities"]))
            last_fired = _geo_seen.get(comm_key)
            if last_fired and last_fired > _cutoff:
                logger.info("GEO dedup: %s (%s) suppressed — fired %s",
                            comm_key, headline[:60], last_fired)
                continue
            logger.info("📰 GEO NEWS → %s: %s", comms, headline)

            # Auto-watch deaktiviert 2026-05-21 (Late-Entry-Pattern, see commit 80d0e30).
            # Oil direction-routing (2026-06-02): both 3OIL (long) + 3OIS (short) fire
            # on the same keywords. Tell Haiku to read the headline's direction and
            # pick the matching instrument — escalation→3OIL, de-escalation/peace→3OIS.
            direction_hint = ""
            if any(c in ("3OIL.MI", "3OIS.MI") for c in event["triggered_commodities"]):
                direction_hint = (
                    " | ÖL-RICHTUNG: Eskalation/Angriff/Sanktion → Öl-hoch → 3OIL.MI (3x long). "
                    "Frieden/Deeskalation/Waffenstillstand → Öl-runter → 3OIS.MI (3x short). "
                    "Wähle das zur News-Richtung passende Instrument. Schon-gelaufen/neutral → PASS."
                )
            ctx = f"GEO NEWS: {headline} | Commodity-Play: {comms}{direction_hint}"
            analyze_portfolio(mode="event", event_context=ctx, force=True)
            _geo_seen[comm_key] = _now_iso
            _dirty = True
            logger.info("GEO news event done — tool handlers sent any Telegrams")

        if _dirty:
            _existing = _dedup.get("geo_news_fired", {}) or {}
            _existing.update(_geo_seen)
            _prune_cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
            _existing = {k: v for k, v in _existing.items() if v > _prune_cutoff}
            _dedup["geo_news_fired"] = _existing
            save_dedup(_dedup)

        # Stock news: filter to open/watch tickers (skip Claude call on irrelevant).
        if stocks and config.NEWS_REQUIRE_OPEN_OR_WATCH:
            _pf = load_portfolio()
            _relevant = (
                {(t.get("ticker") or "").upper() for t in _pf.get("open_trades", [])}
                | {(w.get("ticker") or "").upper() for w in _pf.get("watch_levels", [])}
            )
            _filtered = [
                s for s in stocks
                if (s.get("source_ticker") or "").upper() in _relevant
            ]
            if not _filtered:
                logger.info("Stock news on irrelevant tickers, skipping Claude: %s",
                            [s.get("source_ticker") for s in stocks])
                stocks = []
            else:
                stocks = _filtered

        if stocks:
            headlines = " | ".join(e["headline"] for e in stocks[:3])
            logger.info("📰 STOCK NEWS (%d): %s", len(stocks), headlines[:120])
            ctx = f"NEWS: {headlines}"
            analyze_portfolio(mode="event", event_context=ctx)
            logger.info("Stock news event done — tool handlers sent any Telegrams")

        state.news_check_consec_failures = 0
        state.news_check_alert_sent = False
    except Exception:
        logger.exception("News check failed")
        state.news_check_consec_failures += 1
        if state.news_check_consec_failures >= 3 and not state.news_check_alert_sent:
            try:
                send_alert(
                    "⚠️ News-Pipeline tot",
                    f"{state.news_check_consec_failures}× consecutive failures. "
                    f"Bot ist News-blind bis Fix. Logs prüfen.",
                )
                state.news_check_alert_sent = True
            except Exception:
                logger.exception("News-pipeline alert send failed")
