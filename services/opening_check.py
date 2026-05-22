"""Opening-check service: XETRA/US gap check after market open."""

import logging

from core import analyze_portfolio, load_portfolio, kill_switch_active
from notifier import send_alert

from runtime.scheduler import (
    opening_check_done_today, mark_opening_check_done,
    is_transient_error, transient_retry_inc, transient_retry_reset,
    TRANSIENT_RETRY_CAP,
)


logger = logging.getLogger("trading_advisor.opening_check")


def run_opening_check(market: str) -> None:
    """Lightweight gap-check shortly after market open."""
    if opening_check_done_today(market):
        return

    portfolio = load_portfolio()
    if kill_switch_active(portfolio):
        logger.info("%s open check skipped: kill-switch active", market.upper())
        mark_opening_check_done(market)
        return
    if not portfolio.get("open_trades") and not portfolio.get("watch_levels"):
        logger.info("%s open check skipped — no open trades or watch levels", market.upper())
        mark_opening_check_done(market)
        return

    logger.info("🔔 Running %s open check...", market.upper())

    try:
        label = "XETRA" if market == "xetra" else "US"
        analysis = analyze_portfolio(mode="opening", event_context=f"{label} Open")

        if analysis.startswith("⚠️ Analysis skipped"):
            logger.info("%s open check: %s", market.upper(), analysis)
            return

        # Tool-call-only runtime. analyzer's tool handlers send Telegrams direct.
        logger.info("%s open check done", market.upper())

        transient_retry_reset(f"opening_{market}")
        mark_opening_check_done(market)
    except Exception as e:
        if is_transient_error(e):
            key = f"opening_{market}"
            n = transient_retry_inc(key)
            logger.warning(
                "%s open check transient error (%d/%d): %s",
                market.upper(), n, TRANSIENT_RETRY_CAP, e,
            )
            if n >= TRANSIENT_RETRY_CAP:
                send_alert(
                    f"{market.upper()} Open Check Error",
                    f"{e}\n\n{n} transiente Versuche fehlgeschlagen — aufgegeben.",
                )
                mark_opening_check_done(market)
        else:
            logger.exception("%s open check failed (persistent)", market.upper())
            send_alert(f"{market.upper()} Open Check Error", f"{e}\n\nKein Auto-Retry heute.")
            mark_opening_check_done(market)
