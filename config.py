"""Configuration for the active trading advisor."""

# Trading capital (in EUR)
BUDGET_EUR = 1000.0

# Price check interval (kostenlos via Yahoo Finance)
# Swing-Trading: 15min reicht. yfinance ~15min delayed — schnelleres Polling bringt nix.
PRICE_CHECK_INTERVAL_MINUTES = 15

# Price-alert thresholds (% change vs. previous close)
PRICE_DROP_ALERT_PERCENT = 5.0
PRICE_RISE_ALERT_PERCENT = 5.0

# Market-data cache TTL (seconds) — dedupe yfinance calls within a single loop cycle
MARKET_DATA_CACHE_TTL_SECONDS = 60

# XETRA Feiertage (Markt geschlossen). Halbtags-Tage (24.12., 31.12.) inkludiert für Sicherheit.
# Aktuell halten; neues Jahr manuell ergänzen.
XETRA_HOLIDAYS = {
    # 2026
    "2026-01-01",  # Neujahr
    "2026-04-03",  # Karfreitag
    "2026-04-06",  # Ostermontag
    "2026-05-01",  # Tag der Arbeit
    "2026-12-24",  # Heiligabend (halbtags)
    "2026-12-25",  # 1. Weihnachtsfeiertag
    "2026-12-26",  # 2. Weihnachtsfeiertag
    "2026-12-31",  # Silvester (halbtags)
    # 2027
    "2027-01-01",
    "2027-03-26",  # Karfreitag
    "2027-03-29",  # Ostermontag
    "2027-05-01",
    "2027-12-24",
    "2027-12-27",  # 1. Weihnachtsfeiertag (Ersatz, 25./26. Wochenende)
    "2027-12-31",
}

# Market hours (CET/CEST)
MARKET_OPEN_HOUR = 8
MARKET_CLOSE_HOUR = 22

# Pre-market prep time
MORNING_PREP_HOUR = 8  # Morgen-Analyse um 8:00

# Börsen-Open Checkins (ultra knapp, nur bei Action)
XETRA_OPEN_HOUR = 9
XETRA_OPEN_MINUTE = 5       # 5min nach Kassa-Open (Auktion settled)
US_OPEN_HOUR = 15
US_OPEN_MINUTE = 35         # 5min nach US-Open (15:30 CET)

# Event triggers für automatische Re-Analyse
EVENT_TRIGGER_MOVE_PERCENT = 3.0  # (legacy - BIG_MOVE removed; kept for reference)
BREAKOUT_TRIGGER_PERCENT = 0.5    # Nähe zu Watch-Level (0.5% = fast erreicht)

# Price-Alert Pre-Filter: Claude call nur wenn Ticker Watch-Level hat ODER sehr stark bewegt
STRONG_PRICE_ALERT_PERCENT = 7.0

# Forced-Call Rate-Limit (Geo-News Spam-Schutz)
MIN_MINUTES_BETWEEN_FORCED_ANALYSES = 15

# Earnings Auto-Close Schwelle (Tage bis Earnings)
EARNINGS_CLOSE_DAYS = 1

# Gap-Schwelle (|change_pct| vs prev close) für Morning/Opening Flagging
GAP_FLAG_PERCENT = 2.0

# Sector-Correlation-Gate: max Positionen pro Sektor verhindert Klumpenrisiko
# (z.B. 3× Semis gleichzeitig → Chip-Crash = 3 Stops auf einmal)
MAX_POSITIONS_PER_SECTOR = 2

SECTOR_MAP = {
    # Semiconductors
    "NVD.DE": "semis",
    "AMD.DE": "semis",
    "INL.DE": "semis",
    # Enterprise Software / Cloud
    "MSF.DE": "software_cloud",
    "SAP.DE": "software_cloud",
    # Consumer Tech
    "APC.DE": "consumer_tech",
    # Fintech
    "2PP.DE": "fintech",
    # Internet/Mobility
    "UT8.DE": "internet",
    "SNP.DE": "internet",
    # EV/Auto
    "TL0.DE": "auto_ev",
    # Industrial
    "SIE.DE": "industrial",
    # Finance / Insurance
    "ALV.DE": "insurance",
    # Banking (separate from insurance — rate-sensitive, different macro)
    "DBK.DE": "banking",
    # Pharma / Healthcare
    "BAYN.DE": "pharma",
    # Chemicals
    "BAS.DE": "chemicals",
    # Auto ICE (separate from EV like Tesla)
    "BMW.DE": "auto_ice",
    "MBG.DE": "auto_ice",
    # Utility / Renewables
    "RWE.DE": "utility",
    "ENR.DE": "energy_transition",
    # Aerospace / Defense
    "AIR.DE": "aerospace",
    # Building Materials (cyclical)
    "HEI.DE": "building_materials",
    # Commodities (already risk-diversified by nature but track)
    "3OIL.DE": "oil",
    "4GLD.DE": "gold",
    "EXX1.DE": "silver",
    "U3O8.DE": "uranium",
    "NUKL.DE": "uranium",
    "EXXY.DE": "broad_commodity",
}

# Risk management
MAX_RISK_PER_TRADE_PERCENT = 3.0  # Max % of capital to risk per trade
MAX_POSITION_SIZE_PERCENT = 8.0   # Max % of capital in single position (was 30 — full-trust exec needs tight cap)
MIN_CASH_RESERVE_PERCENT = 20.0   # Always keep this much in cash

# Trade frequency limits
MAX_TRADES_PER_DAY = 2            # Max new trades per day
MAX_ACTIVE_TRADES = 5             # Max open positions at once

# Circuit breakers — halt new entries automatically
DAILY_LOSS_HALT_PERCENT = 5.0     # If realized P&L today ≤ -5% of capital → PASS rest of day
DRAWDOWN_HALT_PERCENT = 8.0       # If equity ≤ peak × (1 - 8%) → PASS-only until recovery
DRAWDOWN_RECOVERY_PERCENT = 2.0   # Resume after equity gains 2% back from halt trough
MAX_PORTFOLIO_HEAT_PERCENT = 10.0 # Max summed open risk (entry-SL) across all positions

# Edge gate — require positive expectancy before recommending entry
MIN_EXPECTED_EDGE = 0.04          # (p*b - (1-p)) ≥ 0.04 else force PASS
KELLY_FRACTION = 0.25             # Quarter-Kelly cap on size_pct

# Execution-quality gates
MAX_ENTRY_SLIPPAGE_PERCENT = 2.0  # /confirm @filled_price rejected if |filled-rec|/rec > 2%

# Liquidity pre-filter (gate tickers before Claude sees them)
MIN_VOLUME_RATIO = 0.3            # current_vol/avg_vol; dead tape if below
MAX_SPREAD_PERCENT = 0.75         # (ask-bid)/price*100; wide spread = bad fill risk

# Regime gate — RISK_OFF blocks new LONG entries (conservative full-trust bias)
RISK_OFF_BLOCKS_LONGS = True

# Valid mistake taxonomy tags (guide classes: prediction/timing/execution/external)
MISTAKE_TAGS = {
    "thesis_wrong",    # prediction error
    "timing_early",    # timing error
    "timing_late",     # timing error
    "slippage",        # execution error
    "news_shock",      # external shock
    "sl_too_tight",    # execution error
    "regime_shift",    # external
    "whipsaw",         # timing
}

# API usage - ROI driven, not hard capped
MAX_ANALYSES_PER_DAY = 20          # Safety cap, but shouldn't hit it normally
MIN_MINUTES_BETWEEN_ANALYSES = 45  # Swing braucht keine Hektik
ANALYSIS_COST_EUR = 0.002          # ~cost per Haiku call for tracking

# Trade Republic constraint: Need 1 full share for Stop-Loss orders
MAX_SHARE_PRICE_FOR_SL = 150.0  # Only auto-suggest SL for stocks under this price

# ❌ AUSGESCHLOSSEN (Sparpläne aktiv - nicht traden!)
EXCLUDED_TICKERS = [
    "PTX.DE",       # Palantir - Sparplan
    "GME",          # GameStop - Sparplan
    "GS2C.DE",      # GameStop XETRA - Sparplan
    "KC=F",         # Kaffee Futures - Position
    # ETFs generell nicht aktiv traden (nur Marktrichtung beobachten)
]

# Watchlist - XETRA Tickers (EUR prices for Trade Republic)
WATCHLIST = [
    # ✅ GÜNSTIG (< €150) - Stop-Loss möglich bei TR
    "2PP.DE",       # PayPal - €43
    "INL.DE",       # Intel - €57
    "UT8.DE",       # Uber - €66
    "SNP.DE",       # Snap - ~€10
    
    # ⚠️ MITTEL (€150-200) - Stop-Loss knapp möglich
    "NVD.DE",       # NVIDIA - €171
    
    # ❌ TEUER (> €200) - Nur manueller Stop-Loss
    "APC.DE",       # Apple - €228
    "AMD.DE",       # AMD - €246
    "MSF.DE",       # Microsoft - €364
    "TL0.DE",       # Tesla - €227
    
    # German/EU Blue Chips
    "SAP.DE",       # SAP - €268
    "SIE.DE",       # Siemens - €213
    "ALV.DE",       # Allianz - €349

    # DAX - Pharma / Healthcare
    "BAYN.DE",      # Bayer - €29 (news-driven, Glyphosat/Pharma-Trials)
    # DAX - Chemicals (cyclical)
    "BAS.DE",       # BASF - €45 (Global Chemicals, China-Zyklus)
    # DAX - Auto ICE (separate von Tesla EV)
    "BMW.DE",       # BMW - €78
    "MBG.DE",       # Mercedes-Benz - €50
    # DAX - Banks (high-beta, rate-sensitive)
    "DBK.DE",       # Deutsche Bank - €20
    # DAX - Utility / Energy Transition
    "RWE.DE",       # RWE - €33 (Stromproduzent, Renewables)
    "ENR.DE",       # Siemens Energy - €50 (Energiewende-Play)
    # DAX - Aerospace
    "AIR.DE",       # Airbus - €160 (Zyklisch, Großaufträge)
    # DAX - Building Materials (cyclical)
    "HEI.DE",       # Heidelberg Materials - €175
]

# 🛢️ ROHSTOFFE - für geopolitische Events (Iran, Krieg, etc.)
# Alle via yfinance verifiziert!
COMMODITIES = [
    # 🛢️ ÖL - Iran/Nahost/OPEC
    "3OIL.DE",      # WisdomTree WTI 3x Long - €1.61 (sehr günstig, 3x Hebel!)
    
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

# Geopolitische Trigger-Map für Alerts
COMMODITY_TRIGGERS = {
    "3OIL.DE": ["iran", "opec", "nahost", "öl", "oil", "saudi", "krieg", "sanktion"],
    "4GLD.DE": ["rezession", "bankenkrise", "fed", "inflation", "crash", "gold"],
    "EXX1.DE": ["silber", "silver", "safe haven", "krise"],
    "U3O8.DE": ["uran", "nuklear", "atom", "smr", "energie"],
    "NUKL.DE": ["uran", "nuklear", "atom", "smr", "energie"],
    "EXXY.DE": ["supply chain", "lieferkette", "inflation", "rohstoff"],
}

# Markt-Indikatoren (nur beobachten, nicht traden)
MARKET_INDICATORS = [
    "SPY5.DE",      # S&P 500 ETF
    "EQQQ.DE",      # Nasdaq 100 ETF
    "^VIX",         # Volatilitätsindex
]

# Mapping: Common names -> XETRA tickers (for convenience)
TICKER_ALIASES = {
    "PYPL": "2PP.DE",
    "INTC": "INL.DE",
    "UBER": "UT8.DE",
    "SNAP": "SNP.DE",
    "PLTR": "PTX.DE",
    "NVDA": "NVD.DE",
    "AAPL": "APC.DE",
    "AMD": "AMD.DE",
    "MSFT": "MSF.DE",
    "TSLA": "TL0.DE",
    "SPY": "SPY5.DE",
    "QQQ": "EQQQ.DE",
}

# Models: Sonnet für Morning-Brief (Senior-Reasoning, 1x/Tag), Haiku für Event-Checks (günstig, schnell)
CLAUDE_MODEL_MORNING = "claude-sonnet-4-6"
CLAUDE_MODEL_EVENT = "claude-haiku-4-5"
CLAUDE_MODEL = CLAUDE_MODEL_EVENT  # Default/Fallback für Standard-Mode
