"""Watchlists, commodities, market indicators, ticker aliases, TR-WKN map,
geo-trigger keyword sets."""

# ❌ AUSGESCHLOSSEN (Sparpläne aktiv - nicht traden!)
EXCLUDED_TICKERS = [
    "PTX.DE",       # Palantir - Sparplan
    "GME",          # GameStop - Sparplan
    "GS2C.DE",      # GameStop XETRA - Sparplan
    "KC=F",         # Kaffee Futures - Position
    # ETFs generell nicht aktiv traden (nur Marktrichtung beobachten)
]

# Watchlist - XETRA Tickers (EUR prices for Trade Republic).
#
# 2026-05-23 reduction (27 → 8 tickers), 2026-05-27 expansion (+3 → 11):
# focused list per user-spec — fewer,
# liquid, clean-trend names beats 27 half-active tickers + huge trigger matrix.
# Removed: too-noisy (BAYN, FRE, DTE, HEN3), redundant auto-cluster (BMW/MBG/
# VOW3/P911/CON kept zero — auto-supplier exposure deferred), tech-US-clones
# (2PP, INL, UT8), small-cap-renewables (NDX1, S92, VH2, VBK).
# Legacy SECTOR_MAP entries kept for migration-safety on existing positions.
WATCHLIST = [
    # 2026-06-02 recalibration: SAP (€161) + AIR (€173) removed — both permanently
    # blocked by whole-share gate (price > ~€100 cap at €1k capital, every cycle).
    # Replaced with BAS/CON/HEN3 (<€80, liquid, new sectors) to widen the
    # actually-tradable universe. 6 days flat was partly structural: half the list
    # was dead weight that could never clear the cap.

    # Semis / AI Infra — best fit for current edge model
    "IFX.DE",       # Infineon (Semis-EU, Auto-/IoT-Exposure)
    "AIXA.DE",      # Aixtron (compound semis, SiC/GaN for AI/EV)
    "SMHN.DE",      # Suss MicroTec (Wafer-Bonder, Fab-Equipment)

    # Banks — high-beta, rate-sensitive, clean trend structure
    "DBK.DE",       # Deutsche Bank
    "CBK.DE",       # Commerzbank

    # Industrial / Infra — boring compounder swings
    "DHL.DE",       # DHL Group (global logistics cycle)
    "RWE.DE",       # RWE (Strom, Renewables)

    # Consumer / Turnaround — opportunistic only, not blind mean-revert
    "PUM.DE",       # Puma (Sportswear, China/brand sentiment)

    # Healthcare — Fresenius, mean-reversion / support_bounce candidate
    "FRE.DE",       # Fresenius SE (Hospital / Infusion / Dialysis)

    # Chemicals — BASF, low-ATR DAX anchor, low correlation to semis+banks
    "BAS.DE",       # BASF SE (Chemie-Zyklik, ~€51, ATR ~2%)

    # Auto / Industrials — Continental, supplier turnaround
    "CON.DE",       # Continental AG (~€73, ATR ~2.4%)

    # Consumer Staples — Henkel, defensive low-vol mean-reversion candidate
    "HEN3.DE",      # Henkel Vz (~€66, ATR ~1.5% — calmest name on the list)
]

# 🛢️ ROHSTOFFE - macro hedge layer (event-driven only)
# 2026-05-23 reduction (7 → 2): keep only the two highest-signal hedges.
# Silver/Uranium/diversified-commodity/Brent dropped — too much noise + thin
# event-correlation at €1k scale.
COMMODITIES = [
    "3OIL.MI",      # WisdomTree WTI 3x Daily Long, TR-WKN A3GM4L (Iran/OPEC/Nahost)
    # 2026-06-02: short-oil counterpart added so geo-news can trade BOTH directions.
    # Long-only bot, but "bearish = inverse_etf_long" (CLAUDE.md) — buying the 3x
    # short ETN IS the structural short. Trump=Frieden → oil down → 3OIS.MI long;
    # Trump=Eskalation → oil up → 3OIL.MI long. Haiku picks direction from headline.
    "3OIS.MI",      # WisdomTree WTI 3x Daily Short ETN (~€1.51, EUR, Milano)
    "4GLD.DE",      # Xetra-Gold (safe haven, Fed/recession/bank-crisis)
]

# Markt-Indikatoren (nur beobachten, nicht traden)
MARKET_INDICATORS = [
    "SPY5.DE",      # S&P 500 ETF
    "EQQQ.DE",      # Nasdaq 100 ETF
    "^VIX",         # Volatilitätsindex
]

# TR-WKN mapping: yfinance-Ticker → Trade-Republic WKN für Telegram-alerts.
# Notwendig wenn User-tradable-Listing andere ISIN/Vintage hat als yfinance-feed.
TR_WKN_MAP = {
    "3OIL.MI": "A3GM4L",  # WisdomTree WTI 3x Daily Long
    # ⚠️ 2026-06-02: WKN/ISIN UNVERIFIED — confirm on Trade Republic before first
    # trade. Candidate: ISIN XS2819844387 / WKN A4AGV3 (WisdomTree WTI 3x Short).
    "3OIS.MI": "A4AGV3",  # WisdomTree WTI 3x Daily Short — VERIFY ON TR
}

# Mapping: Common names -> XETRA tickers (for convenience)
TICKER_ALIASES = {
    "PYPL": "2PP.DE",
    "INTC": "INL.DE",
    "UBER": "UT8.DE",
    "PLTR": "PTX.DE",
    "NVDA": "NVD.DE",
    "AAPL": "APC.DE",
    "AMD": "AMD.DE",
    "MSFT": "MSF.DE",
    "TSLA": "TL0.DE",
    "SPY": "SPY5.DE",
    "QQQ": "EQQQ.DE",
}

# Geopolitische Trigger-Map für Alerts.
# Keywords werden mit \bkw\b matchen (siehe core/events/news.py _COMMODITY_TRIGGER_PATTERNS).
# Daher: Inflexionen explizit listen, keine zu breiten Singles ("crash", "fed", "krise"
# alleine triggerten FPs auf Goldman/Federated/Bankenkrise-Headlines).
# Oil keyword set shared by long (3OIL) + short (3OIS): same headlines fire BOTH,
# Haiku reads direction from the headline and picks long-oil or short-oil.
_OIL_TRIGGERS = [
    "iran", "iranian", "iranische", "iranisch",
    "opec", "opec+",
    "nahost", "middle east",
    "öl", "ölpreis", "rohöl", "crude oil", "brent", "wti",
    "saudi", "saudi-arabien", "saudi arabia",
    "krieg", "war",
    "frieden", "peace", "waffenstillstand", "ceasefire", "deeskalation", "de-escalation",
    "sanktion", "sanktionen", "sanctions",
]

COMMODITY_TRIGGERS = {
    "3OIL.MI": _OIL_TRIGGERS,
    "3OIS.MI": _OIL_TRIGGERS,
    "4GLD.DE": [
        "rezession", "recession",
        "bankenkrise", "banking crisis", "finanzkrise", "financial crisis",
        "fed pivot", "fed cut", "fed cuts", "fed hike", "fed hikes",
        "rate cut", "rate cuts", "rate hike", "rate hikes",
        "zinssenkung", "zinssenkungen", "zinserhöhung", "zinserhöhungen", "leitzins",
        "inflation",
        "market crash", "marktcrash", "stock market crash", "kurssturz",
        "gold", "goldpreis",
    ],
}
