"""Centralized scheduling predicates + transient retry + heartbeat watchdog.

Extracted from main.py (2026-05-22). Single place for time-window logic.
"""

import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic

import config
from core.portfolio import load_portfolio, save_portfolio
from notifier import send_alert

logger = logging.getLogger("trading_advisor.scheduler")


# ============================================================================
# Time-window predicates
# ============================================================================

def is_trading_day(d: date) -> bool:
    """True if `d` is a weekday and not a XETRA holiday."""
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in config.XETRA_HOLIDAYS


def is_market_hours() -> bool:
    """True if within XETRA trading hours (weekday, not a holiday)."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    return config.MARKET_OPEN_HOUR <= now.hour < config.MARKET_CLOSE_HOUR


def is_morning_prep_time() -> bool:
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    return now.hour == config.MORNING_PREP_HOUR and now.minute < 5


def is_xetra_open_check_time() -> bool:
    """5-15min window after XETRA Kassa open."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    return (
        now.hour == config.XETRA_OPEN_HOUR
        and config.XETRA_OPEN_MINUTE <= now.minute < config.XETRA_OPEN_MINUTE + 10
    )


def is_us_open_check_time() -> bool:
    """5-15min window after US market open."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    return (
        now.hour == config.US_OPEN_HOUR
        and config.US_OPEN_MINUTE <= now.minute < config.US_OPEN_MINUTE + 10
    )


def is_weekend_news_window() -> bool:
    """Sunday 18-22 CET — pre-Monday geo-news catch-up."""
    now = datetime.now()
    return now.weekday() == 6 and 18 <= now.hour < 22


def is_eod_summary_time() -> bool:
    """22:10-22:25 CET on trading days."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        return False
    return now.hour == 22 and 10 <= now.minute < 25


def is_weekend_summary_time() -> bool:
    """Sat/Sun 10:00-10:15 CET."""
    now = datetime.now()
    if now.weekday() not in (5, 6):
        return False
    return now.hour == 10 and now.minute < 15


# ============================================================================
# Per-day dedup helpers (persisted in portfolio.json)
# ============================================================================

def morning_prep_done_today() -> bool:
    return load_portfolio().get("last_morning_prep_date") == str(date.today())


def mark_morning_prep_done() -> None:
    pf = load_portfolio()
    pf["last_morning_prep_date"] = str(date.today())
    save_portfolio(pf)


def opening_check_done_today(market: str) -> bool:
    return load_portfolio().get(f"last_{market}_open_check_date") == str(date.today())


def mark_opening_check_done(market: str) -> None:
    pf = load_portfolio()
    pf[f"last_{market}_open_check_date"] = str(date.today())
    save_portfolio(pf)


def eod_summary_done_today() -> bool:
    return load_portfolio().get("last_eod_summary_date") == str(date.today())


def mark_eod_summary_done() -> None:
    pf = load_portfolio()
    pf["last_eod_summary_date"] = str(date.today())
    save_portfolio(pf)


def weekend_summary_done_today() -> bool:
    return load_portfolio().get("last_weekend_summary_date") == str(date.today())


def mark_weekend_summary_done() -> None:
    pf = load_portfolio()
    pf["last_weekend_summary_date"] = str(date.today())
    save_portfolio(pf)


# ============================================================================
# Transient retry — for Anthropic 5xx + network blips
# ============================================================================

TRANSIENT_RETRY_CAP = 3
_TRANSIENT_RETRY_STATUSES = frozenset({429, 502, 503, 504, 529})


def is_transient_error(exc: BaseException) -> bool:
    """True if exc worth retrying."""
    if isinstance(exc, anthropic.APIStatusError):
        return getattr(exc, "status_code", None) in _TRANSIENT_RETRY_STATUSES
    # Recoverable claude-CLI failures (structured-output retry exhaustion,
    # subscription rate-limit). Lazy import to avoid a circular dep with
    # agents._lib.runner. 2026-06-05: US-open crashed on transient Haiku
    # tool-call divergence — should retry next cycle, not alert + give up.
    try:
        from agents._lib.runner import AgentTransientError, AgentRateLimitError
        if isinstance(exc, (AgentTransientError, AgentRateLimitError)):
            return True
    except Exception:
        pass
    return isinstance(exc, (
        anthropic.APIConnectionError, anthropic.APITimeoutError,
        ConnectionError, TimeoutError, OSError,
    ))


def transient_retry_inc(key: str) -> int:
    """Bump today's counter for `key`. Auto-reset on date roll-over.
    Persisted in state/runtime.json (own lock)."""
    from core.portfolio.runtime_store import load_runtime, save_runtime
    runtime = load_runtime()
    bucket = runtime.setdefault("transient_retries", {})
    today = str(date.today())
    entry = bucket.get(key) or {}
    if entry.get("date") != today:
        entry = {"date": today, "count": 0}
    entry["count"] += 1
    bucket[key] = entry
    save_runtime(runtime)
    return entry["count"]


def transient_retry_reset(key: str) -> None:
    from core.portfolio.runtime_store import load_runtime, save_runtime
    runtime = load_runtime()
    bucket = runtime.get("transient_retries") or {}
    if key in bucket:
        bucket.pop(key)
        save_runtime(runtime)


# ============================================================================
# Heartbeat watchdog
# ============================================================================

HEARTBEAT_STALE_SEC = 30 * 60


def heartbeat_watchdog(heartbeat_ref: list[float]) -> None:
    """Background thread. Alert if main loop silent >30min. `heartbeat_ref` is
    1-element list mutated by main loop."""
    alerted = False
    while True:
        try:
            time.sleep(60)
            delta = time.monotonic() - heartbeat_ref[0]
            if delta > HEARTBEAT_STALE_SEC:
                if not alerted:
                    try:
                        send_alert(
                            "Bot Hang",
                            f"Main loop stale {int(delta / 60)} min "
                            f"(letzte Iteration vor {int(delta)}s). "
                            f"launchd restartet bei Crash — manuell prüfen.",
                        )
                    except Exception as exc:
                        logger.error("Heartbeat alert failed: %s", exc)
                    alerted = True
            else:
                alerted = False
        except Exception as exc:
            logger.error("Watchdog tick failed: %s", exc)


# ============================================================================
# Startup cleanup
# ============================================================================

def startup_cleanup() -> None:
    """Prune stale per-day dedup fields + equity_history. One-shot at boot."""
    from core.portfolio import portfolio_lock
    from core.portfolio.dedup_store import load_dedup, save_dedup
    today = str(date.today())
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # ---- Dedup state (state/dedup.json) ----
    try:
        dedup = load_dedup()
        dedup_dirty = False

        sn = dedup.get("seen_news") or {}
        if sn:
            sn_pruned = {k: v for k, v in sn.items() if k in (today, yesterday)}
            if len(sn_pruned) != len(sn):
                dedup["seen_news"] = sn_pruned
                dedup_dirty = True
                logger.info("Cleanup seen_news: %d → %d days", len(sn), len(sn_pruned))

        te = dedup.get("triggered_events") or []
        te_today = [t for t in te if t.get("date") == today]
        if len(te_today) != len(te):
            dedup["triggered_events"] = te_today
            dedup_dirty = True
            logger.info("Cleanup triggered_events: %d → %d", len(te), len(te_today))

        tpa = dedup.get("triggered_price_alerts") or []
        tpa_today = [t for t in tpa if t.get("date") == today]
        if len(tpa_today) != len(tpa):
            dedup["triggered_price_alerts"] = tpa_today
            dedup_dirty = True
            logger.info("Cleanup triggered_price_alerts: %d → %d", len(tpa), len(tpa_today))

        gnf = dedup.get("geo_news_fired") or {}
        if gnf:
            cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
            gnf_pruned = {k: v for k, v in gnf.items() if v > cutoff}
            if len(gnf_pruned) != len(gnf):
                dedup["geo_news_fired"] = gnf_pruned
                dedup_dirty = True
                logger.info("Cleanup geo_news_fired: %d → %d", len(gnf), len(gnf_pruned))

        if dedup_dirty:
            save_dedup(dedup)
            logger.info("Dedup cleanup persisted")
    except Exception:
        logger.exception("Dedup cleanup failed (non-fatal)")

    # ---- Portfolio fields still in portfolio.json (equity_history) ----
    try:
        with portfolio_lock:
            pf = load_portfolio()
            eh = pf.get("equity_history") or []
            if eh:
                cutoff_eh = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M")
                eh_pruned = [p for p in eh if (p.get("ts") or "") >= cutoff_eh]
                if len(eh_pruned) != len(eh):
                    pf["equity_history"] = eh_pruned
                    save_portfolio(pf)
                    logger.info("Cleanup equity_history: %d → %d", len(eh), len(eh_pruned))
    except Exception:
        logger.exception("Portfolio cleanup failed (non-fatal)")

    # ---- Monitoring incident reports (docs/incidents/*.md) ----
    # bug-watcher / health-inspector agents drop one md per run; left unpruned
    # they piled to 121 files and buried real state (2026-06-10). Prune by age
    # (older than 7 days) AND by count (keep at most the newest 20) — the count
    # cap catches burst-days where a single day emits a dozen reports. Sort key
    # is the `YYYY-MM-DD-HHMM-` filename prefix; fall back to mtime if it doesn't
    # parse. unlink() is the bot's own designed cleanup, not an ad-hoc delete.
    try:
        incidents_dir = Path(__file__).resolve().parents[2] / "docs" / "incidents"
        if incidents_dir.is_dir():
            cutoff = date.today() - timedelta(days=7)
            INCIDENT_KEEP_MAX = 20  # newest-N retained regardless of age
            mds = sorted(incidents_dir.glob("*.md"), key=lambda p: p.name, reverse=True)
            removed = 0
            for idx, md in enumerate(mds):
                try:
                    file_date = date.fromisoformat(md.name[:10])
                except ValueError:
                    file_date = date.fromtimestamp(md.stat().st_mtime)
                if file_date < cutoff or idx >= INCIDENT_KEEP_MAX:
                    md.unlink()
                    removed += 1
            if removed:
                logger.info("Cleanup incident reports: removed %d (>7d or beyond newest %d)",
                            removed, INCIDENT_KEEP_MAX)
    except Exception:
        logger.exception("Incident-report cleanup failed (non-fatal)")
