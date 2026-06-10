"""Event-monitor service: watch-level hits + defense-events."""

import logging

import config
from core import (
    analyze_portfolio, detect_events, should_analyze_events,
    load_portfolio, kill_switch_active,
)
from notifier import send_notification, _word_truncate

from runtime.scheduler import is_market_hours


logger = logging.getLogger("trading_advisor.event_monitor")


def run_event_check() -> None:
    """Check for events; route NOTIFY → Telegram-only, HIGH → analyze_portfolio."""
    if not is_market_hours():
        return
    if kill_switch_active(load_portfolio()):
        logger.debug("Event check skipped: kill-switch active")
        return

    try:
        events = detect_events()
        if not events:
            return

        notify_events = [e for e in events if e.get("type") == "WATCH_LEVEL_NOTIFY"]
        analyze_events = [e for e in events if e.get("type") != "WATCH_LEVEL_NOTIFY"]

        if notify_events:
            for ev in notify_events:
                ticker = ev.get("ticker", "?")
                price = ev.get("current_price", 0)
                level_type = ev.get("level_type", "?")
                trigger = ev.get("trigger_price", 0)
                thesis = ev.get("thesis", "")
                source = ev.get("source", "")
                msg = (
                    f"📍 *Watch-Hit: {ticker}* @ €{price:.2f}\n"
                    f"Setup: {level_type} (Trigger €{trigger:.2f})\n"
                )
                if thesis:
                    msg += f"These: {_word_truncate(thesis, 150)}\n"
                if source == "geo_news_auto":
                    msg += "_⚠️ Auto-Watch aus Geo-News — KEIN Sonnet-Plan. Prüfe selbst ob Entry sinnvoll (oft Late/Chase-Pattern)._"
                else:
                    msg += "_Sonnet's Morning-Limit-Buy aktiv? Check /pending_"
                send_notification(msg)
            logger.info(
                "🔔 %d watch-notify(s) sent (Telegram-only): %s",
                len(notify_events),
                ", ".join(e.get("ticker", "?") for e in notify_events),
            )

        if not analyze_events:
            return

        should_analyze, reason = should_analyze_events(analyze_events)
        logger.info("🔔 %d defense/invalidate event(s) - %s", len(analyze_events), reason)

        if not should_analyze:
            return

        event_descriptions = []
        for event in analyze_events:
            desc = f"{event['ticker']} @ ${event['trigger_price']:.2f} ({event['level_type']})"
            if event.get("note"):
                desc += f" - {event['note']}"
            event_descriptions.append(desc)

        event_context = " | ".join(event_descriptions)
        analysis = analyze_portfolio(mode="event", event_context=event_context)

        if analysis.startswith("⚠️ Analysis skipped"):
            logger.info("Event analysis skipped: %s", analysis)
        else:
            logger.info("Event check done — tool handlers sent any Telegrams directly")

    except Exception as e:
        from runtime.scheduler import is_transient_error
        if is_transient_error(e):
            # Recoverable (Haiku tool-call divergence / rate-limit). Retry next
            # cycle — no traceback noise.
            logger.warning("Event check transient error (auto-retry next cycle): %s", e)
        else:
            logger.exception("Event check failed")
