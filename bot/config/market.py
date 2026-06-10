"""Market schedule + price-alert thresholds + cache TTL + holidays."""

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
# 2026-05-25: XETRA_OPEN_MINUTE 5→10 — health-inspector found that at
# 09:05 yfinance volume-feed still showed vol_ratio 0.01 for large-caps
# IFX/DBK → liquidity gate dropped 5/10 tickers. 09:10 gives feed time
# to populate intraday volume.
XETRA_OPEN_HOUR = 9
XETRA_OPEN_MINUTE = 10
US_OPEN_HOUR = 15
# 2026-06-10: US_OPEN_MINUTE 35→42 — at :35 the check fired inside the
# NO_ENTRY_WINDOWS (15:30–15:40) US-open-spillover span, so every opening
# entry was deterministically gate-blocked (review: 80 no_entry_zone blocks).
# :42 lands just after the window → US-open entries can now pass. Detection
# window is [42,52), still caught by the 15-min poll cadence.
US_OPEN_MINUTE = 42

# Event triggers für automatische Re-Analyse
EVENT_TRIGGER_MOVE_PERCENT = 3.0  # (legacy - BIG_MOVE removed; kept for reference)

# Price-Alert Pre-Filter: Claude call nur wenn Ticker Watch-Level hat ODER sehr stark bewegt
STRONG_PRICE_ALERT_PERCENT = 7.0

# Earnings Auto-Close Schwelle (Tage bis Earnings)
EARNINGS_CLOSE_DAYS = 1

# Earnings Hard-Block für neue Entries: T-N bis T+0 (Earnings-Day) blockiert.
# Statistik: Gap-Risiko bei Earnings ist Coin-Flip → kein systematischer Edge.
EARNINGS_ENTRY_BLOCK_DAYS = 2

# Dividenden: Ex-Div Pre-Warning Fenster (Tage). Mechanischer Preis-Drop ≈ Ausschüttung
# kann SL triggern, daher Morning-Brief warnt + schlägt SL-Adjust vor wenn drop ≥ 50% SL-Distance.
DIVIDEND_WARN_DAYS = 7

# Gap-Schwelle (|change_pct| vs prev close) für Morning/Opening Flagging
GAP_FLAG_PERCENT = 2.0
