"""High-impact economic calendar (FOMC / CPI / NFP / ECB / etc.).

Source: Finnhub free tier (https://finnhub.io). Set FINNHUB_API_KEY in .env.
Without a key, fetch returns [] and the feature degrades silently.

We filter to high-impact events in major markets (US/DE/EU/GB). For a full-trust
bot with €1000 capital, only these events are material enough to affect sizing.
"""

import os
import time as _time
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

_UTC = ZoneInfo("UTC")
_BERLIN = ZoneInfo("Europe/Berlin")

logger = logging.getLogger(__name__)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
_CACHE_TTL_SECONDS = 6 * 3600
_cache: dict[str, tuple[list[dict], float]] = {}

# Countries whose high-impact prints actually move XETRA blue-chips / tech.
_MAJOR_COUNTRIES = {"US", "DE", "EU", "GB"}

# True market-movers (can trash swing setups regardless of ticker). Sentiment
# indicators (Ifo, ZEW, Consumer Confidence) excluded — don't block swing entries.
_CRITICAL_KEYWORDS = (
    "fomc", "fed funds", "interest rate decision", "rate decision",
    "cpi", "core cpi", "inflation rate", "ppi",
    "non-farm payrolls", "nonfarm payrolls", "nfp", "unemployment rate",
    "pce",
    "ecb", "bank of england", "boe",
)


def _is_critical(event_name: str) -> bool:
    low = (event_name or "").lower()
    return any(kw in low for kw in _CRITICAL_KEYWORDS)


def fetch_economic_events(days_ahead: int = 1) -> list[dict]:
    """Return high-impact macro events from today through `days_ahead` days.
    Cached for 6h. Returns [] on any failure — caller should treat as best-effort.
    """
    if not FINNHUB_API_KEY:
        return []

    cache_key = f"events_{days_ahead}_{date.today().isoformat()}"
    cached = _cache.get(cache_key)
    if cached and (_time.time() - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    today = date.today()
    end = today + timedelta(days=days_ahead)

    try:
        r = httpx.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": str(today), "to": str(end), "token": FINNHUB_API_KEY},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("Economic calendar fetch failed: %s", e)
        return []

    events = data.get("economicCalendar", [])
    out = []
    for e in events:
        impact = (e.get("impact") or "").lower()
        country = e.get("country", "")
        event_name = e.get("event", "")
        if impact != "high" or country not in _MAJOR_COUNTRIES:
            continue
        if not _is_critical(event_name):
            continue
        raw_time = e.get("time") or ""
        ev_date = raw_time[:10] if len(raw_time) >= 10 else str(today)
        ev_time = raw_time[11:16] if len(raw_time) >= 16 else ""
        out.append({
            "date": ev_date,
            "time": ev_time,
            "event": event_name,
            "country": country,
            "actual": e.get("actual"),
            "estimate": e.get("estimate"),
            "prev": e.get("prev"),
        })

    out.sort(key=lambda e: (e["date"], e["time"]))
    _cache[cache_key] = (out, _time.time())
    return out


def _to_berlin(ev: dict) -> datetime | None:
    """Finnhub times are UTC. Convert to Europe/Berlin (handles DST)."""
    if not ev.get("time"):
        return None
    try:
        utc_dt = datetime.strptime(f"{ev['date']} {ev['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=_UTC)
        return utc_dt.astimezone(_BERLIN)
    except ValueError:
        return None


def today_events() -> list[dict]:
    """Events firing TODAY (Europe/Berlin local date) only."""
    today_local = datetime.now(_BERLIN).date()
    out = []
    for e in fetch_economic_events(days_ahead=1):
        local_dt = _to_berlin(e)
        if local_dt and local_dt.date() == today_local:
            out.append(e)
        elif not local_dt and e["date"] == today_local.isoformat():
            out.append(e)
    return out


def format_events(events: list[dict]) -> str:
    """Compact single-block render for prompt injection. Times in CET/CEST."""
    if not events:
        return ""
    lines = []
    for e in events:
        local = _to_berlin(e)
        time_str = local.strftime("%H:%M") + " " if local else ""
        country = e.get("country", "")
        name = e.get("event", "")
        lines.append(f"  ⚠️ {time_str}{country} — {name}")
    return "\n".join(lines)


def has_imminent_event(events: list[dict], within_hours: int = 3) -> bool:
    """True if any event fires within `within_hours` from now (TZ-aware)."""
    now = datetime.now(_BERLIN)
    for e in events:
        local = _to_berlin(e)
        if local is None:
            continue
        delta = (local - now).total_seconds() / 3600
        if 0 <= delta <= within_hours:
            return True
    return False
