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
# Whole-share-Filter (2026-05-04): nur Tickers <€100 sind handelbar (TR-SL braucht
# ganze Stücke; Cap = total_capital × MAX_POSITION_SIZE_PERCENT/100). Teure Tickers
# (NVD/APC/AMD/MSF/TL0/SAP/SIE/ALV/AIR/HEI) entfernt; Mid-/Small-Caps unter €100
# zugefügt für sektorale Diversifikation. Preise Snapshot 2026-05-04.
WATCHLIST = [
    # Tech US (XETRA-EUR-Listings)
    "2PP.DE",       # PayPal - €43
    "INL.DE",       # Intel - €84
    "UT8.DE",       # Uber - €66

    # Tech EU
    "IFX.DE",       # Infineon - €57 (Semis-EU, Auto-/IoT-Exposure)

    # Semi-Equipment / AI-Infra (added 2026-05-20 — effektive Watchlist war zu
    # eng, 9 von 20 stocks wurden in 5 Mornings nie armiert — mehr AI-Infra
    # Kandidaten für Setup-Vielfalt)
    "AIXA.DE",      # Aixtron - €52 (compound semis, SiC/GaN für AI/EV)
    "WAF.DE",       # Siltronic - €89 (Silicon-Wafer für AI-Chips)
    "SMHN.DE",      # Suss MicroTec - €87 (Wafer-Bonder, Fab-Equipment)

    # Auto (ICE + EV-Exposure)
    "BMW.DE",       # BMW - €78
    "MBG.DE",       # Mercedes-Benz - €50
    "VOW3.DE",      # Volkswagen Vz - €84 (Auto-Massenmarkt)
    "P911.DE",      # Porsche AG - €40 (Luxus-EV/ICE)
    "CON.DE",       # Continental - €61 (Auto-Supplier, Reifen)

    # Banks (high-beta, rate-sensitive)
    "DBK.DE",       # Deutsche Bank - €26
    "CBK.DE",       # Commerzbank - €34

    # Telecom
    "DTE.DE",       # Deutsche Telekom - €27 (Defensive, Dividenden-Anker)

    # Logistics / Industrial
    "DHL.DE",       # DHL Group - €47 (Globaler Logistik-Zyklus)

    # Pharma / Healthcare
    "BAYN.DE",      # Bayer - €37 (news-driven, Glyphosat/Pharma-Trials)
    "FRE.DE",       # Fresenius - €41 (Healthcare-Services)

    # Chemicals
    "BAS.DE",       # BASF - €53 (Global Chemicals, China-Zyklus)
    # 1COV.DE (Covestro) removed 2026-05-18 — delisted after ADNOC squeeze-out,
    # yfinance history empty (5d), bot can't price/trade it.

    # Consumer
    "HEN3.DE",      # Henkel Vz - €62 (Consumer-Goods, Defensive)
    "PUM.DE",       # Puma - €24 (Sportswear, China-/Brand-Sentiment)
    "ZAL.DE",       # Zalando - €21 (E-Commerce-EU)

    # Utility / Energy Transition
    "RWE.DE",       # RWE - €60 (Stromproduzent, Renewables)
    # ENR.DE entfernt 2026-05-04: €178 — über €100 Cap, würde dynamisch gefiltert.
    "NDX1.DE",      # Nordex - €43 (Wind-Turbinen, added 2026-05-20)
    "S92.DE",       # SMA Solar - €62 (Solar-Inverter, added 2026-05-20)
    "VH2.DE",       # Friedrich Vorwerk - €70 (Energie-Infra / H2, added 2026-05-20)
    "VBK.DE",       # VERBIO - €35 (Biofuels, added 2026-05-20)
]

# 🛢️ ROHSTOFFE - für geopolitische Events (Iran, Krieg, etc.)
# Alle via yfinance verifiziert!
COMMODITIES = [
    # 🛢️ ÖL - Iran/Nahost/OPEC
    # WTI + Brent 3x Long (Borsa Italiana yfinance-feed). User trades via TR-WKN
    # A3GM4L (WTI) und A3GM4K (Brent) — gleiche ISIN/Vintage, einfach anderer Markt.
    "3OIL.MI",      # WisdomTree WTI 3x Daily Long, ~€47, TR-WKN A3GM4L
    "3BRL.MI",      # WisdomTree Brent 3x Daily Long, ~€54, TR-WKN A3GM4K

    # 🥇 GOLD - Safe Haven bei Krisen
    "4GLD.DE",      # Xetra-Gold - €130

    # 🥈 SILBER - Safe Haven + Industrial
    "EXX1.DE",      # iShares Physical Silver - €26

    # ☢️ URAN - Nuklear-Renaissance, Energiekrise
    "U3O8.DE",      # Sprott Physical Uranium - €14 (günstig!)
    "NUKL.DE",      # Global X Uranium ETF - €53

    # 📦 BREITE COMMODITIES - Supply Chain, Inflation
    "EXXY.DE",      # iShares Diversified Commodity - €33
]

# Markt-Indikatoren (nur beobachten, nicht traden)
MARKET_INDICATORS = [
    "SPY5.DE",      # S&P 500 ETF
    "EQQQ.DE",      # Nasdaq 100 ETF
    "^VIX",         # Volatilitätsindex
]

# TR-WKN mapping: yfinance-Ticker → Trade-Republic WKN für Telegram-alerts.
# Notwendig wenn User-tradable-Listing andere ISIN/Vintage hat als yfinance-feed.
# Beispiel: 3OIL.MI yfinance-listing = neue 2062-Vintage (auch auf Borsa Italiana),
# aber user kauft auf TR via WKN — Alert zeigt WKN damit user direkt suchen kann.
TR_WKN_MAP = {
    "3OIL.MI": "A3GM4L",  # WisdomTree WTI 3x Daily Long
    "3BRL.MI": "A3GM4K",  # WisdomTree Brent 3x Daily Long
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
    # Brent 3x: gleiche Geo-Trigger wie WTI (Märkte korreliert), Brent ist
    # europe/middle-east-bias → leicht stärker auf MENA-news, daher zusätzlich
    # russia/ukraine/north-sea Trigger.
    "3BRL.MI": [
        "iran", "iranian", "iranische", "iranisch",
        "opec", "opec+",
        "nahost", "middle east",
        "öl", "ölpreis", "rohöl", "crude oil", "brent", "wti",
        "saudi", "saudi-arabien", "saudi arabia",
        "krieg", "war",
        "sanktion", "sanktionen", "sanctions",
        "russland", "russia", "ukraine",
        "north sea", "nordsee",
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
    "EXX1.DE": [
        "silber", "silver", "silberpreis",
        "safe haven",
        "bankenkrise", "banking crisis",
    ],
    "U3O8.DE": [
        "uran", "uranium",
        "nuklear", "nuclear", "kernkraft", "atomkraft", "atomenergie",
        "smr", "small modular reactor",
    ],
    "NUKL.DE": [
        "uran", "uranium",
        "nuklear", "nuclear", "kernkraft", "atomkraft", "atomenergie",
        "smr", "small modular reactor",
    ],
    "EXXY.DE": [
        "supply chain", "lieferkette",
        "supply shock", "lieferengpass",
        "rohstoffknappheit", "raw material shortage",
    ],
}
