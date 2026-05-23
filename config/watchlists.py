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
# 2026-05-23 reduction (27 → 8 tickers): focused list per user-spec — fewer,
# liquid, clean-trend names beats 27 half-active tickers + huge trigger matrix.
# Removed: too-noisy (BAYN, FRE, DTE, HEN3), redundant auto-cluster (BMW/MBG/
# VOW3/P911/CON kept zero — auto-supplier exposure deferred), tech-US-clones
# (2PP, INL, UT8), small-cap-renewables (NDX1, S92, VH2, VBK).
# Legacy SECTOR_MAP entries kept for migration-safety on existing positions.
WATCHLIST = [
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
]

# 🛢️ ROHSTOFFE - macro hedge layer (event-driven only)
# 2026-05-23 reduction (7 → 2): keep only the two highest-signal hedges.
# Silver/Uranium/diversified-commodity/Brent dropped — too much noise + thin
# event-correlation at €1k scale.
COMMODITIES = [
    "3OIL.MI",      # WisdomTree WTI 3x Daily Long, TR-WKN A3GM4L (Iran/OPEC/Nahost)
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
COMMODITY_TRIGGERS = {
    "3OIL.MI": [
        "iran", "iranian", "iranische", "iranisch",
        "opec", "opec+",
        "nahost", "middle east",
        "öl", "ölpreis", "rohöl", "crude oil", "brent", "wti",
        "saudi", "saudi-arabien", "saudi arabia",
        "krieg", "war",
        "sanktion", "sanktionen", "sanctions",
    ],
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
