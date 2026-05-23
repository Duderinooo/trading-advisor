"""Event dedup + no-entry-window helpers.

Watch-level events are deduped via a TTL-based key (ticker + trigger_price + type)
persisted in portfolio.triggered_events. TTL-based instead of per-day so a watch
whose first hit got dropped by a downstream gate gets a polite re-try after the
cooldown elapses.
"""

import logging
from datetime import date, datetime

import config
from core.events.types import EventType
from core.portfolio import portfolio_lock, load_portfolio, save_portfolio


logger = logging.getLogger(__name__)


def get_event_key(event: dict) -> str:
    """Unique key per event for dedup."""
    et = event["type"]
    if et == EventType.WATCH_LEVEL_HIT:
        return f"watch_{event['ticker']}_{event['trigger_price']}"
    if et == EventType.WATCH_LEVEL_NOTIFY:
        return f"watch_notify_{event['ticker']}_{event['trigger_price']}"
    if et == EventType.WATCH_INVALIDATED:
        return f"watch_invalid_{event['ticker']}_{event.get('invalidate_below')}"
    return str(event)


def is_event_already_triggered(event_key: str, portfolio: dict) -> bool:
    """TTL-based dedup. Bug 2026-05-07: per-day dedup made watch blind for the
    full day after one bad analyzer pass."""
    triggered = portfolio.get("triggered_events", [])
    now = datetime.now()
    ttl_min = config.EVENT_DEDUP_TTL_MIN
    for t in triggered:
        if t.get("key") != event_key:
            continue
        ts = t.get("ts")
        if ts:
            try:
                fired = datetime.strptime(ts, "%Y-%m-%d %H:%M")
                if (now - fired).total_seconds() / 60 < ttl_min:
                    return True
                continue  # expired entry — same key may re-fire
            except ValueError:
                pass
        # Legacy entry (no ts, only date) — treat as still-active for today only.
        if t.get("date") == str(date.today()):
            return True
    return False


def mark_events_triggered(events: list[dict]) -> None:
    """Persist `ts` (full timestamp) for TTL-based dedup. Pruned to today's date
    so triggered_events doesn't grow unbounded across days."""
    with portfolio_lock:
        fresh = load_portfolio()
        today = str(date.today())
        triggered = [t for t in fresh.get("triggered_events", []) if t.get("date") == today]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        for event in events:
            key = get_event_key(event)
            # Replace stale entry for this key (older than TTL) instead of keeping it.
            triggered = [t for t in triggered if t.get("key") != key]
            triggered.append({
                "key": key,
                "date": today,
                "ts": now_str,
                "time": now_str.split(" ")[1],
            })
        fresh["triggered_events"] = triggered
        save_portfolio(fresh)


def in_no_entry_window(now: datetime) -> bool:
    """True if `now` falls inside a config.NO_ENTRY_WINDOWS span.
    Mirrors analyzer's no_entry_zone gate (gate #3) so a watch-hit landing in an
    auction/EOD window is dropped before it burns a Haiku call on a doomed rec."""
    cur = now.hour * 60 + now.minute
    for sh, sm, eh, em in config.NO_ENTRY_WINDOWS:
        if sh * 60 + sm <= cur < eh * 60 + em:
            return True
    return False
