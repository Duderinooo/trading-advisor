"""High-impact economic calendar (FOMC / CPI / NFP / ECB / etc.).

Source: ForexFactory's free weekly JSON feed (mirrored by faireconomy.media).
No API key, no rate limit. 2026-06-16: switched off Finnhub — its
`/calendar/economic` endpoint moved behind a paid tier (403 on free keys),
which left the macro pre-release entry-block blind. Fetch returns [] on any
failure and the feature degrades silently.

We filter to high-impact events in major markets (US/EU/GB). For a full-trust
bot with €1000 capital, only these events are material enough to affect sizing.
"""

import time as _time
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

_UTC = ZoneInfo("UTC")
_BERLIN = ZoneInfo("Europe/Berlin")

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 6 * 3600
_cache: dict[str, tuple[list[dict], float]] = {}

# ForexFactory weekly feed. Only `thisweek` is published reliably (nextweek
# 404s); day-ahead usage (days_ahead=1) stays within the current week anyway.
_FF_URLS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
)
_FF_UA = "Mozilla/5.0 (compatible; trading-advisor/1.0)"

# ForexFactory tags events by currency, not country — map to our country codes.
# German prints surface under EUR (ECB/EU-CPI cover the material ones).
_CCY_TO_COUNTRY = {"USD": "US", "EUR": "EU", "GBP": "GB"}

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


def _parse_ff_datetime(raw: str | None) -> tuple[str | None, str]:
    """ForexFactory date is ISO-8601 with a UTC offset (e.g.
    '2026-06-14T18:30:00-04:00'). Return (YYYY-MM-DD, HH:MM) in UTC to match the
    shape _to_berlin expects. (None, '') if unparseable."""
    if not raw:
        return (None, "")
    try:
        dt = datetime.fromisoformat(raw).astimezone(_UTC)
        return (dt.date().isoformat(), dt.strftime("%H:%M"))
    except (ValueError, TypeError):
        return (None, "")


def fetch_economic_events(days_ahead: int = 1) -> list[dict]:
    """Return high-impact macro events from today through `days_ahead` days.
    Cached for 6h. Returns [] on any failure — caller should treat as best-effort.
    """
    cache_key = f"events_{days_ahead}_{date.today().isoformat()}"
    cached = _cache.get(cache_key)
    if cached and (_time.time() - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    today = date.today()
    end = today + timedelta(days=days_ahead)
    today_iso, end_iso = today.isoformat(), end.isoformat()

    raw_events: list[dict] = []
    for url in _FF_URLS:
        try:
            r = httpx.get(url, headers={"User-Agent": _FF_UA}, timeout=10.0)
            r.raise_for_status()
            raw_events.extend(r.json())
        except Exception as e:
            # nextweek can fail without breaking thisweek; log + continue.
            logger.warning("Economic calendar fetch failed (%s): %s", url, e)

    out = []
    seen: set = set()
    for e in raw_events:
        if (e.get("impact") or "").lower() != "high":
            continue
        country = _CCY_TO_COUNTRY.get((e.get("country") or "").upper())
        if country not in _MAJOR_COUNTRIES:
            continue
        event_name = e.get("title") or ""
        if not _is_critical(event_name):
            continue
        ev_date, ev_time = _parse_ff_datetime(e.get("date"))
        if ev_date is None or not (today_iso <= ev_date <= end_iso):
            continue
        key = (ev_date, ev_time, event_name, country)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "date": ev_date,
            "time": ev_time,
            "event": event_name,
            "country": country,
            "actual": e.get("actual"),
            "estimate": e.get("forecast"),
            "prev": e.get("previous"),
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
