#!/usr/bin/env python3
"""
Event-Driven Trading Advisor Bot
Monitors markets and triggers analysis when important events happen.
"""

import os
import time
import logging
import signal
import sys
import threading
from datetime import datetime, date
from logging.handlers import RotatingFileHandler

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
    get_dividend_warnings,
    check_news_events,
    kill_switch_active,
    portfolio_lock,
    get_market_data,
)
from memory import log_trade, MEMPALACE_AVAILABLE
from notifier import send_notification, send_daily_summary, send_alert
from telegram_listener import start_listener_thread


_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Root logger → rotating file. No StreamHandler: launchd's bot.err only catches
# pre-logging-init crashes (small) instead of the ~MB/day yfinance noise we saw before.
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
for _h in list(_root_logger.handlers):
    _root_logger.removeHandler(_h)
_file_handler = RotatingFileHandler(_LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
_root_logger.addHandler(_file_handler)

# Mute noisy 3rd-party loggers (httpx logs every Telegram poll at INFO).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

logger = logging.getLogger("trading_advisor")


# Heartbeat: main-loop tick timestamp. Watchdog thread alerts via Telegram if
# the loop is silent for >30 min during market hours. launchd KeepAlive only
# covers process death — this catches hangs (deadlock, stuck network call).
_HEARTBEAT_STALE_SEC = 30 * 60
_heartbeat = [time.monotonic()]


def _heartbeat_watchdog():
    alerted = False
    while True:
        try:
            time.sleep(60)
            delta = time.monotonic() - _heartbeat[0]
            if delta > _HEARTBEAT_STALE_SEC:
                if not alerted:
                    try:
                        send_alert(
                            "Bot Hang",
                            f"Main loop stale {int(delta / 60)} min "
                            f"(letzte Iteration vor {int(delta)}s). "
                            f"launchd restartet bei Crash, aber Prozess lebt → manuell prüfen.",
                        )
                    except Exception as exc:
                        logger.error("Heartbeat alert failed: %s", exc)
                    alerted = True
            else:
                alerted = False
        except Exception as exc:
            logger.error("Watchdog tick failed: %s", exc)


_ACTIONABLE_PREFIXES = ("ENTRY", "EXIT", "BUY", "SELL", "KAUFEN", "VERKAUFEN", "CLOSE")


def _is_actionable(analysis: str) -> bool:
    """True only if Claude's verdict requires user action. PASS/HALTEN → False (no notify)."""
    if not analysis:
        return False
    first = analysis.strip().split("\n", 1)[0].strip().upper().lstrip("*• `")
    if first.startswith("PASS") or first.startswith("HALTEN") or first.startswith("HOLD"):
        return False
    return any(first.startswith(p) for p in _ACTIONABLE_PREFIXES)


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


def is_weekend_news_window() -> bool:
    """Sunday 18:00-22:00 CET — pre-Monday geo-news catch-up window.
    Catches weekend events (Iran strike, OPEC surprise) early enough to queue
    a Monday-open entry. Single scan per hour is enough."""
    now = datetime.now()
    return now.weekday() == 6 and 18 <= now.hour < 22


def is_eod_summary_time() -> bool:
    """22:10–22:25 CET on trading days. After US close, before midnight rollover."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return now.hour == 22 and 10 <= now.minute < 25


def _eod_summary_done_today() -> bool:
    return load_portfolio().get("last_eod_summary_date") == str(date.today())


def _mark_eod_summary_done():
    portfolio = load_portfolio()
    portfolio["last_eod_summary_date"] = str(date.today())
    save_portfolio(portfolio)


def run_eod_summary():
    """One-shot EOD digest: realized P&L, open positions w/ unrealized, gates state."""
    if _eod_summary_done_today():
        return
    try:
        portfolio = load_portfolio()
        today = str(date.today())

        closed_today = [
            t for t in portfolio.get("closed_trades", [])
            if (t.get("exit_date") or "").startswith(today)
        ]
        realized_eur = sum(float(t.get("pnl_eur") or 0) for t in closed_today)
        wins_today = sum(1 for t in closed_today if (t.get("pnl_eur") or 0) > 0)

        open_trades = portfolio.get("open_trades", [])
        unrealized_eur = 0.0
        if open_trades:
            try:
                live = get_market_data([t["ticker"] for t in open_trades])
                for t in open_trades:
                    price = (live.get(t["ticker"]) or {}).get("price") if isinstance(live.get(t["ticker"]), dict) else None
                    entry = float(t.get("entry_price") or 0)
                    shares = float(t.get("shares") or 0)
                    if isinstance(price, (int, float)) and entry > 0:
                        unrealized_eur += (price - entry) * shares
            except Exception:
                logger.exception("EOD live-pull failed")

        from core import get_daily_usage
        calls_today = get_daily_usage()
        ks_state = "🛑 AKTIV" if kill_switch_active(portfolio) else "✅ aus"
        dd_state = "🚫 DD-LATCH" if portfolio.get("dd_halt_active") else "—"

        cash = float(portfolio.get("cash_eur") or 0)
        starting = float(portfolio.get("total_capital_eur") or config.BUDGET_EUR)
        equity_realized = starting + sum(float(t.get("pnl_eur") or 0) for t in portfolio.get("closed_trades", []))
        equity_total = equity_realized + unrealized_eur

        msg = (
            f"📊 *EOD {today}*\n\n"
            f"Realized today: €{realized_eur:+.2f} ({len(closed_today)} closed, {wins_today}W)\n"
            f"Unrealized: €{unrealized_eur:+.2f} ({len(open_trades)} offen)\n"
            f"Cash: €{cash:.2f} | Equity: €{equity_total:.2f} (Start €{starting:.2f})\n\n"
            f"Claude calls: {calls_today}/{config.MAX_ANALYSES_PER_DAY}\n"
            f"Kill-Switch: {ks_state} | DD-Halt: {dd_state}"
        )
        send_notification(msg)
        _mark_eod_summary_done()
        logger.info("✅ EOD summary sent")
    except Exception:
        logger.exception("EOD summary failed")


def is_us_open_check_time() -> bool:
    """5–15min window after US market open (15:30 CET)."""
    now = datetime.now()
    if not _is_trading_day(now.date()):
        return False
    return (
        now.hour == config.US_OPEN_HOUR
        and config.US_OPEN_MINUTE <= now.minute < config.US_OPEN_MINUTE + 10
    )


def _check_stale_theses():
    """Alert on positions held longer than `hold_days_max` from rec.
    Why: thesis horizon is 3-7d for swing; positions drifting beyond signal a hope-trade.
    Re-fires once per day at most via stale_alerted_date persistence."""
    today = str(date.today())
    with portfolio_lock:
        portfolio = load_portfolio()
        dirty = False
        for trade in portfolio.get("open_trades", []):
            hold_max = trade.get("hold_days_max")
            entry_date = trade.get("entry_date")
            if not isinstance(hold_max, (int, float)) or not entry_date:
                continue
            try:
                entry_d = datetime.strptime(entry_date[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            days_held = (date.today() - entry_d).days
            if days_held < hold_max:
                continue
            if trade.get("stale_alerted_date") == today:
                continue
            ticker = trade.get("ticker", "?")
            send_alert(
                f"🕒 STALE THESIS: {ticker}",
                f"Held {days_held}d > Thesis-Horizon {int(hold_max)}d.\n"
                f"Thesis: _{trade.get('thesis','—')}_\n"
                f"Entscheide: schließen, re-analyzen, oder Trailing-Stop straffen.",
            )
            logger.warning("Stale thesis alert: %s held %dd > %d", ticker, days_held, hold_max)
            trade["stale_alerted_date"] = today
            dirty = True
        if dirty:
            save_portfolio(portfolio)


def run_morning_prep():
    """Run morning analysis to prepare for the trading day."""
    if _morning_prep_done_today():
        return

    logger.info("☀️ Running morning prep...")

    try:
        _check_stale_theses()
    except Exception:
        logger.exception("Stale-thesis check failed")

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

    # Ex-dividend pre-check: mechanical price gap on ex-date can trip SL falsely.
    # Warn for open positions with ex-div in <=DIVIDEND_WARN_DAYS, flag if div eats >50% of SL distance.
    try:
        open_trades = load_portfolio().get("open_trades", [])
        if open_trades:
            by_ticker = {t["ticker"]: t for t in open_trades}
            warns = get_dividend_warnings(list(by_ticker.keys()), days_ahead=config.DIVIDEND_WARN_DAYS)
            for w in warns:
                trade = by_ticker.get(w["ticker"])
                if not trade:
                    continue
                entry = trade.get("entry_price")
                sl = trade.get("stop_loss")
                div = w["expected_div"]
                msg = (
                    f"Ex-Div in *{w['days_until']} Tag(en)* ({w['ex_date']})\n"
                    f"Erwartete Ausschüttung: ~€{div:.2f}/Aktie\n"
                )
                sl_threat = False
                if entry and sl and entry > sl:
                    sl_distance = entry - sl
                    drop_share = div / sl_distance if sl_distance > 0 else 0
                    msg += (
                        f"SL-Distance: €{sl_distance:.2f} | Ex-Div-Drop frisst {drop_share*100:.0f}% davon\n"
                    )
                    if drop_share >= 0.5:
                        sl_threat = True
                        suggested_sl = round(sl - div, 2)
                        msg += (
                            f"⚠️ Mechanischer Drop kann SL triggern.\n"
                            f"Vorschlag: SL temporär auf €{suggested_sl:.2f} senken (heute Abend), "
                            f"nach Ex-Div ({w['ex_date']}) zurücksetzen."
                        )
                title = (
                    f"💸 EX-DIV WARNUNG: {w['ticker']} — SL-Risiko"
                    if sl_threat else
                    f"💸 Ex-Div anstehend: {w['ticker']}"
                )
                send_alert(title, msg)
                logger.info(
                    "Dividend warning: %s ex=%s div=%.2f sl_threat=%s",
                    w["ticker"], w["ex_date"], div, sl_threat,
                )
    except Exception:
        logger.exception("Dividend pre-check failed")

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
    if kill_switch_active(load_portfolio()):
        logger.debug("Event check skipped: kill-switch active")
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
        elif _is_actionable(analysis):
            alert_msg = f"🚨 *WATCH LEVEL HIT*\n" + "\n".join(f"• {d}" for d in event_descriptions)
            send_notification(alert_msg)
            send_daily_summary(analysis)
            logger.info("✅ Event verdict sent: %s", analysis.split('\n')[0][:80])
        else:
            logger.info("Event non-actionable, suppressed: %s", analysis.split('\n')[0][:80])

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

            elif alert["type"] == "TRAILING_ACTIVATED":
                send_notification(
                    f"📐 *Trailing aktiviert: {alert['ticker']}*\n\n"
                    f"Trail: {alert['trail_pct']}% (1.5× ATR {alert['atr_pct']}%)\n"
                    f"_Runner-Schutz: SL zieht ab jetzt automatisch nach._"
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
        # Skipped under kill-switch (no new entries; SL/TP loop above keeps running).
        price_alerts = [] if kill_switch_active(load_portfolio()) else check_price_alerts()

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
            elif _is_actionable(analysis):
                send_notification(f"💹 *PREIS-ALERT*\n\n{event_context}\n\n{analysis}")
                logger.info("✅ Price alert verdict sent: %s", analysis.split('\n')[0][:80])
            else:
                logger.info("Price alert non-actionable, suppressed: %s", analysis.split('\n')[0][:80])
    except Exception:
        logger.exception("Price check failed")


def _auto_watch_geo(commodities: list[str], headline: str):
    """Set breakout-long watch-levels at +5% on matched commodities (GEO trigger).
    Skips tickers that already have an active watch-level."""
    if not commodities:
        return
    market = get_market_data(commodities)
    with portfolio_lock:
        portfolio = load_portfolio()
        existing = {w.get("ticker") for w in portfolio.get("watch_levels", [])}
        added = []
        for ticker in commodities:
            if ticker in existing:
                continue
            data = market.get(ticker, {})
            price = data.get("price") if isinstance(data, dict) else None
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            trigger = round(price * 1.05, 2)
            portfolio.setdefault("watch_levels", []).append({
                "ticker": ticker,
                "type": "breakout_long",
                "trigger_price": trigger,
                "note": f"GEO-Auto: {headline[:60]}",
                "source": "geo_news_auto",
                "created_date": str(date.today()),
            })
            added.append(f"{ticker}@€{trigger:.2f}")
        if added:
            save_portfolio(portfolio)
            logger.info("GEO auto-watch added: %s", ", ".join(added))


def run_news_check():
    """Scan for new actionable headlines. Geo news forces analysis; stock news respects cooldown.
    Runs during market hours + Sunday 18-22 CET (weekend geo-news catch-up)."""
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

        # Geopolitical: bypass cooldown but notify only on actionable verdict
        for event in geo:
            comms = ", ".join(event["triggered_commodities"])
            headline = event["headline"]
            logger.info("📰 GEO NEWS → %s: %s", comms, headline)

            # Auto-add breakout watch-levels for matched commodities so the breakout
            # gets caught by detect_events even if Claude says PASS this round.
            # Why: GEO news is the trigger; the move often comes hours later — we need
            # persistent levels, not a one-shot Claude call.
            try:
                _auto_watch_geo(event["triggered_commodities"], headline)
            except Exception:
                logger.exception("GEO auto-watch failed")

            ctx = f"GEO NEWS: {headline} | Commodity-Play: {comms}"
            analysis = analyze_portfolio(mode="event", event_context=ctx, force=True)
            if _is_actionable(analysis):
                send_notification(
                    f"📰 *GEO NEWS ALERT*\n\n_{headline}_\n\n🎯 Relevante Titel: `{comms}`"
                )
                send_daily_summary(analysis)
            else:
                logger.info("GEO news non-actionable, suppressed: %s", (analysis or "").split('\n')[0][:80])

        # Stock news: always route to Claude. Output filter (_is_actionable) suppresses
        # PASS/HALTEN so user only sees ENTRY/EXIT. Dropping news at input cost real
        # signal (e.g. INL.DE earnings +20% without active watch-level).
        if stocks:
            headlines = " | ".join(e["headline"] for e in stocks[:3])
            tickers = list({e["source_ticker"] for e in stocks})
            logger.info("📰 STOCK NEWS (%d): %s", len(stocks), headlines[:120])
            ctx = f"NEWS: {headlines}"
            analysis = analyze_portfolio(mode="event", event_context=ctx)
            if _is_actionable(analysis):
                send_notification(f"📰 *NEWS ALERT* — {', '.join(tickers)}\n\n_{headlines}_")
                send_daily_summary(analysis)
            else:
                logger.info("Stock news non-actionable, suppressed: %s", (analysis or "").split('\n')[0][:80])

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
    logger.info("Weekend News Scan: Sonntag 18-22 CET (geo-news catch-up)")
    logger.info("=" * 50)

    # Background Telegram listener for /confirm, /close, /positions, /cancel
    start_listener_thread()

    # Background hang-detection watchdog. Daemon=True → dies with main process.
    threading.Thread(target=_heartbeat_watchdog, daemon=True, name="heartbeat-watchdog").start()

    if is_morning_prep_time() or (is_market_hours() and not _morning_prep_done_today()):
        run_morning_prep()

    logger.info("⏰ Monitoring aktiv. Ctrl+C zum Stoppen.")
    
    last_check = datetime.now()
    check_interval = config.PRICE_CHECK_INTERVAL_MINUTES * 60

    while True:
        _heartbeat[0] = time.monotonic()
        now = datetime.now()

        if is_morning_prep_time():
            run_morning_prep()

        # Time-window checks: each fires once per day (dedup via portfolio.json)
        if is_xetra_open_check_time():
            run_opening_check("xetra")
        if is_us_open_check_time():
            run_opening_check("us")
        if is_eod_summary_time():
            run_eod_summary()

        if (now - last_check).total_seconds() >= check_interval:
            if is_market_hours():
                run_price_check()
                run_event_check()
                run_news_check()
            elif is_weekend_news_window():
                # Sunday evening: news-only scan, no price/event checks (markets closed)
                run_news_check()

            last_check = now

        time.sleep(30)


if __name__ == "__main__":
    main()
