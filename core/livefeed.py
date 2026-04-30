"""Lang & Schwarz Tradecenter live-quote scraper (ls-tc.de).

Server-renders the current Lightstreamer snapshot into HTML on every page hit.
We GET the per-instrument page during market hours and parse the bid/ask/mid
fields. Fresher than yfinance (15min delay) — same Market-Maker that prices
Trade Republic, so quotes match what the user sees in TR.

Public site, robots.txt allows /, no auth. Single-user, low-volume use only.
Fail-soft: any error returns None and caller falls back to yfinance.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# yfinance does NOT provide ISIN for `.DE` Tradegate symbols (returns "-").
# Hardcoded map for our universe — kept here so the lookup has a single source
# of truth. Add new tickers when WATCHLIST/COMMODITIES grows.
TICKER_ISIN_MAP: dict[str, str] = {
    # US-Stocks listed on Tradegate / XETRA
    "2PP.DE":  "US70450Y1038",  # PayPal
    "INL.DE":  "US4581401001",  # Intel
    "UT8.DE":  "US90353T1007",  # Uber
    "NVD.DE":  "US67066G1040",  # NVIDIA
    "APC.DE":  "US0378331005",  # Apple
    "AMD.DE":  "US0079031078",  # AMD
    "MSF.DE":  "US5949181045",  # Microsoft
    "TL0.DE":  "US88160R1014",  # Tesla
    # German blue chips
    "SAP.DE":  "DE0007164600",
    "SIE.DE":  "DE0007236101",  # Siemens
    "ALV.DE":  "DE0008404005",  # Allianz
    "BAYN.DE": "DE000BAY0017",  # Bayer
    "BAS.DE":  "DE000BASF111",  # BASF
    "BMW.DE":  "DE0005190003",
    "MBG.DE":  "DE0007100000",  # Mercedes-Benz
    "DBK.DE":  "DE0005140008",  # Deutsche Bank
    "RWE.DE":  "DE0007037129",
    "ENR.DE":  "DE000ENER6Y0",  # Siemens Energy
    "AIR.DE":  "NL0000235190",  # Airbus
    "HEI.DE":  "DE0006047004",  # Heidelberg Materials
    # Commodities / ETFs
    "4GLD.DE": "DE000A0S9GB0",  # Xetra-Gold
    "EXX1.DE": "IE00B4NCWG09",  # iShares Physical Silver
    "U3O8.DE": "CA85207H1047",  # Sprott Physical Uranium
    "NUKL.DE": "IE000NDWFGA5",  # Global X Uranium ETF
}


_BASE = "https://www.ls-tc.de"
_SEARCH_URL = f"{_BASE}/_rpc/json/.lstc/instrument/search/main"
_INSTRUMENT_URL_FMT = f"{_BASE}/de/aktie/{{id}}"
_TIMEOUT_SEC = 3.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15"
)
_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9",
}

_ID_CACHE_PATH = Path(__file__).resolve().parent.parent / ".ls_tc_ids.json"
_ID_CACHE: dict[str, int] | None = None
# Negative cache: ISINs we already failed to resolve (avoid hot-loop re-asks).
_NEGATIVE_CACHE: set[str] = set()
# Per-process quote cache to absorb burst reads (events.py + heartbeat hit
# the same ticker within seconds). Short TTL — we want "fresh-ish".
_QUOTE_CACHE: dict[int, tuple[float, dict]] = {}
_QUOTE_TTL_SEC = 30.0


def _load_id_cache() -> dict[str, int]:
    global _ID_CACHE
    if _ID_CACHE is not None:
        return _ID_CACHE
    if _ID_CACHE_PATH.exists():
        try:
            _ID_CACHE = json.loads(_ID_CACHE_PATH.read_text())
            return _ID_CACHE
        except Exception:
            logger.warning("ls_tc_ids cache corrupt, starting fresh")
    _ID_CACHE = {}
    return _ID_CACHE


def _save_id_cache() -> None:
    if _ID_CACHE is None:
        return
    try:
        _ID_CACHE_PATH.write_text(json.dumps(_ID_CACHE, sort_keys=True, indent=2))
    except OSError as e:
        logger.warning("ls_tc_ids cache save failed: %s", e)


def _resolve_instrument_id(isin: str) -> int | None:
    """Look up Lang & Schwarz instrument-id by ISIN. Cached to file."""
    if not isin or len(isin) != 12:
        return None
    cache = _load_id_cache()
    if isin in cache:
        return cache[isin]
    if isin in _NEGATIVE_CACHE:
        return None
    try:
        r = requests.get(
            _SEARCH_URL,
            params={"q": isin, "localeId": 2},
            headers=_HEADERS,
            timeout=_TIMEOUT_SEC,
        )
        if r.status_code != 200:
            logger.info("LS-TC search %s status=%d", isin, r.status_code)
            _NEGATIVE_CACHE.add(isin)
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            _NEGATIVE_CACHE.add(isin)
            return None
        # Prefer Aktie (categorySymbol=STK); else first.
        stk = next((d for d in data if d.get("categorySymbol") == "STK"), None)
        chosen = stk or data[0]
        iid = chosen.get("id") or chosen.get("instrumentId")
        if not isinstance(iid, int):
            _NEGATIVE_CACHE.add(isin)
            return None
        cache[isin] = iid
        _save_id_cache()
        return iid
    except (requests.RequestException, ValueError) as e:
        logger.warning("LS-TC search failed for %s: %s", isin, e)
        return None


def _parse_de_number(s: str | None) -> float | None:
    """German formatted '60,2900' → 60.29. Returns None on garbage."""
    if not s:
        return None
    s = s.strip().replace("\xa0", " ").replace(" ", "")
    s = s.replace("%", "").replace("€", "").replace("+", "")
    s = s.replace(".", "").replace(",", ".")  # 1.234,56 → 1234.56
    try:
        return float(s)
    except ValueError:
        return None


_FIELD_RE = re.compile(r'\bfield="([^"]+)"')


def _extract_quote_fields(html: str) -> dict[str, Any]:
    """Find every <span source="lightstreamer" ... field="X">VAL</span>
    and return {field: parsed-value}. Numeric fields auto-parsed."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {}
    for tag in soup.find_all("span", attrs={"source": "lightstreamer"}):
        field = tag.get("field")
        if not field or field in out:
            continue
        # Some fields wrap an inner span (text-success/text-danger) — get all text.
        text = tag.get_text(strip=True)
        out[field] = text
    return out


def _market_status(html: str) -> str | None:
    """Parse 'Status: open/closed' badge. Returns 'open'/'closed'/None."""
    soup = BeautifulSoup(html, "html.parser")
    # The status badge sits in a <span class="btn-xs btn btn-primary"> next to <b>Status:</b>.
    for b in soup.find_all("b"):
        if b.get_text(strip=True).startswith("Status"):
            sib = b.find_next("span")
            if sib:
                t = sib.get_text(strip=True).lower()
                if t in ("open", "closed"):
                    return t
    return None


def get_live_quote(isin: str) -> dict | None:
    """Fetch live bid/ask/mid for an ISIN from ls-tc.de.

    Returns None on any failure. Result dict:
        {
          "price": float,        # mid
          "bid": float,
          "ask": float,
          "bid_size": int | None,
          "ask_size": int | None,
          "change_abs": float | None,
          "change_pct": float | None,
          "ts": str,             # HH:MM:SS local from page
          "market_status": "open"|"closed"|None,
          "source": "ls-tc",
          "instrument_id": int,
          "fetched_at": float,   # time.time() epoch seconds
        }
    """
    iid = _resolve_instrument_id(isin)
    if iid is None:
        return None
    now = time.monotonic()
    cached = _QUOTE_CACHE.get(iid)
    if cached and (now - cached[0]) < _QUOTE_TTL_SEC:
        return cached[1]
    url = _INSTRUMENT_URL_FMT.format(id=iid)
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT_SEC, allow_redirects=True)
        if r.status_code != 200:
            logger.info("LS-TC quote %s (%s) status=%d", isin, iid, r.status_code)
            return None
        fields = _extract_quote_fields(r.text)
        if "mid" not in fields and "bid" not in fields:
            return None
        quote = {
            "price": _parse_de_number(fields.get("mid")),
            "bid": _parse_de_number(fields.get("bid")),
            "ask": _parse_de_number(fields.get("ask")),
            "bid_size": int(_parse_de_number(fields.get("bidSize")) or 0) or None,
            "ask_size": int(_parse_de_number(fields.get("askSize")) or 0) or None,
            "change_abs": _parse_de_number(fields.get("midPerf1d")),
            "change_pct": _parse_de_number(fields.get("midPerf1dRelWithPercentSign")),
            "ts": fields.get("midTime"),
            "market_status": _market_status(r.text),
            "source": "ls-tc",
            "instrument_id": iid,
            "fetched_at": time.time(),
        }
        if quote["price"] is None and quote["bid"] is not None and quote["ask"] is not None:
            quote["price"] = (quote["bid"] + quote["ask"]) / 2.0
        if quote["price"] is None:
            return None
        _QUOTE_CACHE[iid] = (now, quote)
        return quote
    except requests.RequestException as e:
        logger.warning("LS-TC quote fetch failed %s: %s", isin, e)
        return None


def clear_caches() -> None:
    """Test/maintenance helper — drops in-memory caches (negative + quote)."""
    _QUOTE_CACHE.clear()
    _NEGATIVE_CACHE.clear()
