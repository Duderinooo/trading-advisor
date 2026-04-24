#!/usr/bin/env python3
"""
Event-Driven Trading Advisor Bot
Monitors markets and triggers analysis when important events happen.
"""

import time
import logging
import signal
import sys
from datetime import datetime, date

import config
from core import (
    analyze_portfolio,
    check_price_alerts,
    check_stop_loss_take_profit,
    detect_events,
    should_analyze_events,
    load_portfolio,
    save_portfolio,
    get_earnings_warnings,
    check_news_events,
)
from memory import log_trade, MEMPALACE_AVAILABLE
from notifier import send_notification, send_daily_summary, send_alert
from telegram_listener import start_listener_thread


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("trading_advisor")


def _morning_prep_done_today() -> bool:
    """Check if morning prep already ran today (persisted in portfolio.json)."""
    portfolio = load_portfolio()
    return portfolio.get("last_morning_prep_date") == str(date.today())


def _mark_morning_prep_done():
    portfolio = load_portfolio()
    portfolio["last_morning_prep_date"] = str(date.today())
    save_portfolio(portfolio)


def _opening_check_done_today(market: str) -> bool:
    portfolio = load_portfolio()
    return portfolio.get(f"last_{market}_open_check_date") == str(date.today())


def _mark_opening_check_done(market: str):
    portfolio = load_portfolio()
    portfolio[f"last_{market}_open_check_date"] = str(date.today())
    save_portfolio(portfolio)


def _is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in config.XETRA_HOLIDAYS


def is_market_hours() -> bool:
    """True if within XETRA trading hours (weekday, not a holiday)."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return config.MARKET_OPEN_HOUR <= now.hour < config.MARKET_CLOSE_HOUR


def is_morning_prep_time() -> bool:
    """Check if it's time for morning prep (weekday, non-holiday)."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return now.hour == config.MORNING_PREP_HOUR and now.minute < 5


def is_xetra_open_check_time() -> bool:
    """5–15min window after XETRA Kassa open."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return (
        now.hour == config.XETRA_OPEN_HOUR
        and config.XETRA_OPEN_MINUTE <= now.minute < config.XETRA_OPEN_MINUTE + 10
    )


def is_us_open_check_time() -> bool:
    """5–15min window after US market open (15:30 CET)."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return (
        now.hour == config.US_OPEN_HOUR
        and config.US_OPEN_MINUTE <= now.minute < config.US_OPEN_MINUTE + 10
    )


def run_morning_prep():
    """Run morning analysis to prepare for the trading day."""
    if _morning_prep_done_today():
        return

    logger.info("☀️ Running morning prep...")

    # Direct earnings alert for open positions — fires before Claude, guaranteed delivery
    try:
        open_tickers = [t["ticker"] for t in load_portfolio().get("open_trades", [])]
        if open_tickers:
            for w in get_earnings_warnings(open_tickers, days_ahead=2):
                if w["days_until"] <= config.EARNINGS_CLOSE_DAYS:
                    send_alert(
                        f"🚨 EARNINGS MORGEN: {w['ticker']} — CLOSE EMPFOHLEN",
                        f"Earnings in *{w['days_until']} Tag(en)* ({w['earnings_date']})\n"
                        f"⚠️ Position JETZT schließen oder auf 25% Size reduzieren.\n"
                        f"Grund: Overnight-Gap-Risiko (IV crush + unbekannte Richtung).",
                    )
                    logger.warning("Earnings CLOSE recommended: %s in %d days", w["ticker"], w["days_until"])
                else:
                    send_alert(
                        f"⚠️ EARNINGS: {w['ticker']}",
                        f"Earnings in *{w['days_until']} Tag(en)* ({w['earnings_date']})\n"
                        f"Offene Position! Vor Earnings schließen oder Size reduzieren.",
                    )
                    logger.warning("Earnings warning sent: %s in %d days", w["ticker"], w["days_until"])
    except Exception:
        logger.exception("Earnings pre-check failed")

    try:
        analysis = analyze_portfolio(mode="morning")
        send_daily_summary(analysis)
        _mark_morning_prep_done()
        logger.info("✅ Morning prep sent")
    except (ConnectionError, TimeoutError, OSError) as e:
        logger.exception("Morning prep failed (network/IO)")
        send_alert("Morning Prep Error", str(e))
    except Exception as e:
        logger.exception("Morning prep failed (unexpected)")
        send_alert("Morning Prep Error", str(e))


def run_opening_check(market: str):
    """Lightweight gap-check shortly after market open.
    Only sends a notification when Claude flags real action."""
    if _opening_check_done_today(market):
        return

    portfolio = load_portfolio()
    if not portfolio.get("open_trades") and not portfolio.get("watch_levels"):
        logger.info("%s open check skipped — no open trades or watch levels", market.upper())
        _mark_opening_check_done(market)
        return

    logger.info("🔔 Running %s open check...", market.upper())

    try:
        label = "XETRA" if market == "xetra" else "US"
        analysis = analyze_portfolio(mode="opening", event_context=f"{label} Open")

        if analysis.startswith("⚠️ Analysis skipped"):
            logger.info("%s open check: %s", market.upper(), analysis)
            return

        stripped = analysis.strip().lower()
        is_stable = (
            stripped.startswith("alles stabil")
            or stripped == "keine anpassungen"
            or len(stripped) < 40
        )

        if is_stable:
            logger.info("%s open: stabil, no notification", market.upper())
        else:
            send_daily_summary(f"🔔 *{label} OPEN CHECK*\n\n{analysis}")
            logger.info("✅ %s open check sent (action flagged)", market.upper())

        _mark_opening_check_done(market)
    except Exception as e:
        logger.exception("%s open check failed", market.upper())
        send_alert(f"{market.upper()} Open Check Error", str(e))


def run_event_check():
    """Check for events and trigger analysis if needed."""
    if not is_market_hours():
        return

    try:
        events = detect_events()

        if not events:
            return

        # detect_events now returns WATCH_LEVEL_HIT only (BIG_MOVE removed).
        should_analyze, reason = should_analyze_events(events)

        logger.info("🔔 %d watch level(s) hit - %s", len(events), reason)

        if not should_analyze:
            return

        event_descriptions = []
        for event in events:
            desc = f"{event['ticker']} @ ${event['trigger_price']:.2f} ({event['level_type']})"
            if event.get("note"):
                desc += f" - {event['note']}"
            event_descriptions.append(desc)

        event_context = " | ".join(event_descriptions)

        analysis = analyze_portfolio(mode="event", event_context=event_context)

        # Always forward Claude's verdict. Event prompt enforces ENTRY/EXIT/PASS format —
        # all three are actionable info for the full-trust user. Skip only if:
        #   - cooldown skipped the call
        #   - Claude called a tool without text (the tool itself sent Telegram)
        if analysis.startswith("⚠️ Analysis skipped"):
            logger.info("Event analysis skipped: %s", analysis)
        elif "(keine Text-Analyse)" in analysis:
            logger.info("Event: tool-only call, Telegram already sent by tool handler")
        else:
            alert_msg = f"🚨 *WATCH LEVEL HIT*\n" + "\n".join(f"• {d}" for d in event_descriptions)
            send_notification(alert_msg)
            send_daily_summary(analysis)
            logger.info("✅ Event verdict sent: %s", analysis.split('\n')[0][:80])

    except Exception:
        logger.exception("Event check failed")


def run_price_check():
    """Check for price alerts and stop-loss/take-profit triggers."""
    if not is_market_hours():
        return

    logger.debug("Checking prices...")

    try:
        # Check stop-loss and take-profit for open trades
        sl_tp_alerts = check_stop_loss_take_profit()
        
        for alert in sl_tp_alerts:
            if alert["type"] == "STOP_LOSS_HIT":
                message = f"""🚨 *STOP-LOSS HIT: {alert['ticker']}*

Entry: ${alert['entry']:.2f}
Stop: ${alert['stop_loss']:.2f}
Now: ${alert['current_price']:.2f}
P&L: {alert['pnl_pct']:.1f}%

⚠️ *CLOSE POSITION NOW*"""
                send_notification(message)
                
                # Log to MemPalace
                if MEMPALACE_AVAILABLE:
                    log_trade(alert, "STOP_HIT", f"Stop-Loss getriggert bei ${alert['current_price']:.2f}")
                
            elif alert["type"] == "TAKE_PROFIT_HIT":
                partial_note = "TP1 (Teil-Verkauf)" if alert.get("partial") else "Full TP"
                message = f"""🎉 *TAKE-PROFIT HIT: {alert['ticker']}* ({partial_note})

Entry: ${alert['entry']:.2f}
Target: ${alert['take_profit']:.2f}
Now: ${alert['current_price']:.2f}
P&L: +{alert['pnl_pct']:.1f}%

💰 *TAKE PROFITS*"""
                send_notification(message)

                if MEMPALACE_AVAILABLE:
                    action = "TP1_HIT" if alert.get("partial") else "TP_HIT"
                    log_trade(alert, action, f"Take-Profit erreicht bei ${alert['current_price']:.2f}")

            elif alert["type"] == "BREAK_EVEN_SHIFT":
                send_notification(
                    f"🛡️ *Stop auf Break-Even: {alert['ticker']}*\n\n"
                    f"Neuer Stop: ${alert['new_stop']:.2f}\n"
                    f"_Rest-Position läuft risikofrei weiter._"
                )

            elif alert["type"] == "TRAILING_STOP_MOVED":
                send_notification(
                    f"📈 *Trailing-Stop nachgezogen: {alert['ticker']}*\n\n"
                    f"Neuer Stop: ${alert['new_stop']:.2f}\n"
                    f"Preis: ${alert['current_price']:.2f}"
                )

            elif alert["type"] == "STOP_LOSS_WARNING":
                message = f"""⚠️ *Approaching Stop: {alert['ticker']}*

Stop: ${alert['stop_loss']:.2f}
Now: ${alert['current_price']:.2f}
Distance: {alert['distance_pct']:.1f}%

_Watch closely_"""
                send_notification(message)
        
        # Price alerts → Claude analysis → only notify if actionable (BUY/SELL)
        price_alerts = check_price_alerts()

        if price_alerts:
            descriptions = []
            for a in price_alerts:
                emoji = "📉" if a["type"] == "DROP" else "📈"
                descriptions.append(
                    f"{emoji} {a['ticker']} {a['change']:+.1f}% @ {a['price']:.2f}"
                )
            event_context = " | ".join(descriptions)
            logger.info("🔔 %d price alert(s): %s", len(price_alerts), event_context)

            analysis = analyze_portfolio(mode="event", event_context=event_context)

            # Always forward Claude's verdict (ENTRY/EXIT/PASS). Full-trust user needs
            # to see the decision, not just silent "no keyword matched".
            if analysis.startswith("⚠️ Analysis skipped"):
                logger.info("Price alert analysis skipped: %s", analysis)
            elif "(keine Text-Analyse)" in analysis:
                logger.info("Price alert: tool-only call, Telegram already sent by tool handler")
            else:
                send_notification(f"💹 *PREIS-ALERT*\n\n{event_context}\n\n{analysis}")
                logger.info("✅ Price alert verdict sent: %s", analysis.split('\n')[0][:80])
    except Exception:
        logger.exception("Price check failed")


def run_news_check():
    """Scan for new actionable headlines. Geo news forces analysis; stock news respects cooldown."""
    if not is_market_hours():
        return

    try:
        news_events = check_news_events()
        if not news_events:
            return

        geo = [e for e in news_events if e["type"] == "NEWS_GEO"]
        stocks = [e for e in news_events if e["type"] == "NEWS_STOCK"]

        # Geopolitical: fire immediately, bypass cooldown — time-sensitive
        for event in geo:
            comms = ", ".join(event["triggered_commodities"])
            headline = event["headline"]
            logger.info("📰 GEO NEWS → %s: %s", comms, headline)
            send_notification(f"📰 *GEO NEWS ALERT*\n\n_{headline}_\n\n🎯 Relevante Titel: `{comms}`")
            ctx = f"GEO NEWS: {headline} | Commodity-Play: {comms}"
            analysis = analyze_portfolio(mode="event", event_context=ctx, force=True)
            analysis_upper = analysis.upper()
            if any(kw in analysis_upper for kw in ("KAUFEN", "BUY", "ENTRY")):
                send_daily_summary(analysis)

        # Stock news: respect cooldown, batch into one analysis
        if stocks:
            headlines = " | ".join(e["headline"] for e in stocks[:3])
            tickers = list({e["source_ticker"] for e in stocks})
            logger.info("📰 STOCK NEWS (%d): %s", len(stocks), headlines[:120])
            ctx = f"NEWS: {headlines}"
            analysis = analyze_portfolio(mode="event", event_context=ctx)
            analysis_upper = analysis.upper()
            if any(kw in analysis_upper for kw in ("KAUFEN", "BUY", "ENTRY", "SELL", "VERKAUFEN")):
                send_notification(f"📰 *NEWS ALERT* — {', '.join(tickers)}\n\n_{headlines}_")
                send_daily_summary(analysis)

    except Exception:
        logger.exception("News check failed")


def graceful_shutdown(signum, frame):
    """Handle shutdown gracefully."""
    logger.info("👋 Shutting down Trading Advisor...")
    sys.exit(0)


def main():
    """Main entry point."""
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    logger.info("=" * 50)
    logger.info("💹 Event-Driven Trading Advisor")
    logger.info("=" * 50)
    logger.info("Kapital: €%s", config.BUDGET_EUR)
    logger.info("Watchlist: %s", ", ".join(config.WATCHLIST))
    logger.info("Rohstoffe: %s", ", ".join(config.COMMODITIES))
    logger.info("Price Check: Jede %d Min", config.PRICE_CHECK_INTERVAL_MINUTES)
    logger.info("Morning Prep: %d:00 Uhr", config.MORNING_PREP_HOUR)
    logger.info(
        "Opening Checks: XETRA %d:%02d / US %d:%02d",
        config.XETRA_OPEN_HOUR, config.XETRA_OPEN_MINUTE,
        config.US_OPEN_HOUR, config.US_OPEN_MINUTE,
    )
    logger.info("=" * 50)

    # Background Telegram listener for /confirm, /close, /positions, /cancel
    start_listener_thread()

    if is_morning_prep_time() or (is_market_hours() and not _morning_prep_done_today()):
        run_morning_prep()

    logger.info("⏰ Monitoring aktiv. Ctrl+C zum Stoppen.")
    
    last_check = datetime.now()
    check_interval = config.PRICE_CHECK_INTERVAL_MINUTES * 60

    while True:
        now = datetime.now()

        if is_morning_prep_time():
            run_morning_prep()

        # Time-window checks: each fires once per day (dedup via portfolio.json)
        if is_xetra_open_check_time():
            run_opening_check("xetra")
        if is_us_open_check_time():
            run_opening_check("us")

        if (now - last_check).total_seconds() >= check_interval:
            if is_market_hours():
                run_price_check()
                run_event_check()
                run_news_check()

            last_check = now

        time.sleep(30)


if __name__ == "__main__":
    main()
