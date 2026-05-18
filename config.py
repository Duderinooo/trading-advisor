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
# Bumped 0.5→1.0 (2026-05-07): 3OIL.MI -20.9% intraday war 0.56% über Trigger
# (146.66 vs 145.84) → 0.06pp über Threshold = ungetriggert. 1.0% fängt
# Overshoot-Cases ein ohne dass Watches ständig vorzeitig feuern.
BREAKOUT_TRIGGER_PERCENT = 1.0    # Nähe zu Watch-Level (1.0% = approaching)
# Confirm-Close-Above Toleranz: bei tag-and-no-confirm (price 1ct unter Sonnet's
# confirm_close_above) doch noch triggern. 2026-05-07: INL.DE 94.47 vs 94.48 →
# 11× heute knapp gemissed. 0.1% slack = €0.094 bei 94.20-Trigger, real-life
# Spread/Tick-Granularität, kein semantischer Bruch der Confirm-Logik.
CONFIRM_CLOSE_TOLERANCE_PCT = 0.1
# Event-Dedup-TTL (Minuten): wie lange ein getriggerter Event-Key blockiert,
# bevor derselbe Watch erneut feuern darf. War bisher per-Tag (zu lang —
# wenn analyzer den ersten Hit verwarf, blieb Watch bis zum nächsten Tag
# blind. Bug 2026-05-07: edge-gate haircut killte CON.DE +9% → triggered
# permanent für Tag, kein Retry möglich nach Fix). 60min = polite re-fire.
EVENT_DEDUP_TTL_MIN = 60

# Price-Alert Pre-Filter: Claude call nur wenn Ticker Watch-Level hat ODER sehr stark bewegt
STRONG_PRICE_ALERT_PERCENT = 7.0

# Forced-Call Rate-Limit (Geo-News Spam-Schutz)
MIN_MINUTES_BETWEEN_FORCED_ANALYSES = 15

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

# Sector-Correlation-Gate: max Positionen pro Sektor verhindert Klumpenrisiko
# (z.B. 3× Semis gleichzeitig → Chip-Crash = 3 Stops auf einmal)
MAX_POSITIONS_PER_SECTOR = 2

SECTOR_MAP = {
    # Semiconductors
    "NVD.DE": "semis",       # legacy: open positions only (gefiltert für neue Entries)
    "AMD.DE": "semis",       # legacy
    "INL.DE": "semis",
    "IFX.DE": "semis",
    # Enterprise Software / Cloud
    "MSF.DE": "software_cloud",  # legacy
    "SAP.DE": "software_cloud",  # legacy
    # Consumer Tech
    "APC.DE": "consumer_tech",   # legacy
    # Fintech
    "2PP.DE": "fintech",
    # Internet/Mobility
    "UT8.DE": "internet",
    # E-commerce
    "ZAL.DE": "ecommerce",
    # EV/Auto
    "TL0.DE": "auto_ev",         # legacy
    # Industrial
    "SIE.DE": "industrial",      # legacy
    # Finance / Insurance
    "ALV.DE": "insurance",       # legacy
    # Banking
    "DBK.DE": "banking",
    "CBK.DE": "banking",
    # Telecom
    "DTE.DE": "telecom",
    # Logistics
    "DHL.DE": "logistics",
    # Pharma / Healthcare
    "BAYN.DE": "pharma",
    "FRE.DE": "healthcare",
    # Chemicals
    "BAS.DE": "chemicals",
    # Auto (ICE + Massenmarkt)
    "BMW.DE": "auto_ice",
    "MBG.DE": "auto_ice",
    "VOW3.DE": "auto_ice",
    "P911.DE": "auto_luxury",
    "CON.DE": "auto_supplier",
    # Consumer Goods
    "HEN3.DE": "consumer_goods",
    "PUM.DE": "consumer_goods",
    # Utility / Renewables
    "RWE.DE": "utility",
    "ENR.DE": "energy_transition",
    # Aerospace / Defense
    "AIR.DE": "aerospace",       # legacy
    # Building Materials (cyclical)
    "HEI.DE": "building_materials",  # legacy
    # Commodities (already risk-diversified by nature but track)
    "3OIL.MI": "oil",
    "3BRL.MI": "oil",   # Brent 3x — same sector cluster as WTI for diversification gate
    "4GLD.DE": "gold",
    "EXX1.DE": "silver",
    "U3O8.DE": "uranium",
    "NUKL.DE": "uranium",
    "EXXY.DE": "broad_commodity",
}

# Risk management
MAX_RISK_PER_TRADE_PERCENT = 3.0  # Max % of capital to risk per trade
# Sanity-cap auf Position-Größe — bindet selten, real-Size kommt aus ATR-Risk +
# Kelly-Fraction + Conviction (siehe core.portfolio.suggest_position_size).
# 2026-05-07: 10→30 nach decouple (MAX_SHARE_PRICE_EUR ersetzte 10% als share-price
# filter). Bot's adaptive Kelly + ATR-Sizing soll Position-Größe steuern, nicht
# eine harte 10%-Wand die jede mid-cap-Position abwürgt.
# 2026-05-12: 30→20 — bei R-Multiple 0.81 (Loser > Winner) und 1k Kapital sind
# 30% pro Position emotional + finanziell zu groß. 20% erlaubt mehr parallele
# Trades = schnellere Sample-Akkumulation + Diversifikation.
MAX_POSITION_SIZE_PERCENT = 20.0  # Sanity ceiling — Kelly/ATR/Conviction drive real size
# Share-Price-Filter: Aktien teurer als €100 sind un-traded weil TR-SL nur auf
# ganze Stücke geht. €100 Cap erlaubt ≥1 Stk auch bei kleinem Kapital. Entkoppelt
# von Position-Size-Cap (war vorher beides aus MAX_POSITION_SIZE_PERCENT abgeleitet).
MAX_SHARE_PRICE_EUR = 100.0
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
# 2026-05-12: 0.04→0.06 — strenger Edge-Filter bei Fee-Drag von €2/Trade auf 1k.
# Nur Trades mit dickem mathematischem Vorteil sollen durch — quality > quantity.
MIN_EXPECTED_EDGE = 0.06          # (p*b - (1-p)) ≥ 0.06 else force PASS
KELLY_FRACTION = 0.25             # Quarter-Kelly cap on size_pct

# 2026-05-13: Brier-Haircut auf p_win nur ab statistisch belastbarer Sample-Größe.
# Bei n<10 ist bias = Noise — drunter würde haircut willkürlich p_win shiften.
# Aktueller Bot-Stand n=1, bias=-0.38 → Bot würde ohne diesen Floor jedem p_win
# +20% (gecapped) drauflegen basierend auf einem einzigen Brier-Sample.
MIN_CALIBRATION_N = 10            # Min scored Trades für Haircut-Aktivierung

# 2026-05-17: Entry-Gate-Cooldown. RS-20d + edge sind intraday-stabil — failt ein
# Ticker eines dieser Gates, ist Re-Eval in derselben Stunde sinnlos (BAS.DE 05-15:
# 3× RS-Block in 2h). Cooldown markiert den Ticker im market_data-Dump, Claude
# überspringt ihn. Hard-Gate bleibt als Backstop auf Live-Daten — improved sich RS
# echt, lässt der Live-Check trotzdem durch (Cooldown ist Hinweis, kein Hard-Block).
ENTRY_GATE_COOLDOWN_MIN = 240     # 4h: kein erneuter Entry-Eval-Hinweis nach RS/edge-Fail

# TR-Fixkosten pro Order: €1 Kauf + €1 Verkauf = €2 Roundtrip. Bei kleinen Positionen
# (€80–100) frisst Fee einen merklichen Anteil am 1R-Gewinn → Gate fordert Brutto-
# Gewinn @ TP1 ≥ Fees + MIN_NET_PROFIT_EUR. Verhindert Null-Summen-Trades nach Kosten.
FIXED_FEE_EUR_PER_SIDE = 1.0      # Trade Republic Order-Gebühr pro Seite
MIN_NET_PROFIT_EUR = 2.0          # Mindest-Netto-Gewinn nach Fees am TP1 (auf whole-shares × (TP1-entry))

# Exit-Reminder Schedule (User-Feedback 2026-05-04: ein Reminder reicht, danach
# Auto-Drop). Flow: 1. Reminder bei Urgency-Threshold → AUTO_DROP_GAP Pause →
# Auto-Drop + Trade-Markierung. Cooldown verhindert dass Claude in nächster Analysis
# sofort wieder Exit-Rec für gleichen Ticker generiert.
EXIT_AUTO_DROP_GAP_MIN = 60             # Pause zwischen Reminder und Auto-Drop
EXIT_REC_COOLDOWN_MIN_AFTER_DROP = 240  # 4h: keine neue Exit-Rec für Ticker nach Drop

# Exit-Confirmation Gate (2026-05-13): thesis-decay Exit-Recs werden suppressed
# wenn intraday-Snapshot nicht durch Multi-Signal bestätigt ist. Verhindert
# Whipsaw-Exits wie PUM.DE 2026-05-12 (Bot exit @ 24.58 wegen RS-Collapse +
# RSI 38, nächster Tag +4.8% Rip, TP1 26.73 fast getagged).
# Hard-Exits (SL-hit, Earnings-Defense, Panic) bleiben unbeeinflusst.
EXIT_GATE_MIN_VOL_RATIO = 0.5      # Echter Selloff = vol_ratio ≥ 0.5; drunter = niemand verkauft = whipsaw
EXIT_GATE_MIN_RSI = 35             # RSI < 35 = oversold-Bounce-Zone → warten statt exit
EXIT_GATE_MAX_VWAP_DEV_ATR = -0.5  # Price ≥ -0.5 ATR von VWAP = mild, kein Panic → warten

# Execution-quality gates
MAX_ENTRY_SLIPPAGE_PERCENT = 2.0  # /confirm @filled_price rejected if |filled-rec|/rec > 2%
PENDING_REC_TTL_HOURS = 4         # Rec veraltet nach 4h — Preis weg, Kontext veraltet → reject
SL_SLIPPAGE_TAG_PERCENT = 1.0     # Exit ≥1% unter SL → auto-tag slippage (execution-class)
MIN_SL_DISTANCE_ATR = 0.8         # SL näher als 0.8×ATR → Whipsaw-garantiert, block
MAX_SL_DISTANCE_ATR = 3.0         # SL weiter als 3×ATR → Risk-Reward kaputt, block
SL_WARN_DISTANCE_PCT = 1.0        # STOP_LOSS_WARNING wenn current_price ≤ SL × (1+pct/100)

# No-Entry-Zonen (CET): Auction-Spike Open + EOD-Chop Close = schlechteste Fill-Quality.
# Format: (start_hour, start_min, end_hour, end_min). Nur entry_recommendation blocked,
# SL/TP-Monitoring + Watch-Level laufen weiter.
NO_ENTRY_WINDOWS = [
    (9, 0, 9, 10),    # XETRA open auction spike
    (15, 30, 15, 40), # US open spillover
    (17, 10, 17, 30), # XETRA close chop
    (21, 40, 22, 0),  # US close chop
]

# Liquidity pre-filter (gate tickers before Claude sees them)
MIN_VOLUME_RATIO = 0.3            # current_vol/avg_vol; dead tape if below
MAX_SPREAD_PERCENT = 0.75         # (ask-bid)/price*100; wide spread = bad fill risk
# XETRA Open: nur 5min Volumen aggregiert vs Tagesschnitt = winzig. Ohne Lockerung
# fallen halbe Watchlist (DBK/SAP/BAS/BAYN) raus → Opening-Check sieht nichts.
MIN_VOLUME_RATIO_OPENING = 0.05

# Big-Mover-Bypass: Price-Alert mit |change| ≥ Threshold darf Cooldown brechen.
# Sonst frisst Morning-Prep (08:00) den Cooldown bis 08:14 → NVD/AMD-Gaps werden
# komplett verschluckt (Bug 2026-05-04 audit).
BIG_MOVER_PCT_BYPASS = 4.0

# Breakout-Volume-Confirmation: setup_type=breakout_resistance braucht Volumen-Bestätigung.
# Ohne Volumen = Fake-Breakout, hohe Whipsaw-Rate.
# 2026-05-07: 1.3 zu strikt für XETRA Mid-Caps + early-day-volume.
# INL.DE @94.20 vol-gate fail bei 0.34 < 1.30 — selbst "above-average" (1.0)
# reicht für Breakout-Confirm bei diesen Liquiditäts-Profilen.
MIN_BREAKOUT_VOLUME_RATIO = 1.0   # heute_volume / avg_volume

# Relative-Strength Gate: für LONG-Entries muss Ticker ≥ Index in den letzten 20 Tagen
# performen. Filter gegen Lagger im Aufwärtstrend. Override bei mean_reversion-Setups.
# 2026-05-12: -1.0→0.0 — nur Outperformer-Stocks. Lagger im Aufwärtstrend
# haben empirisch schlechtere Forward-Returns. PUM.DE entry hatte -0.57pp RS,
# danach auf -5.8pp gecrasht → genau das was diese Schwelle verhindern soll.
MIN_RS_20D_VS_INDEX_PCT = 0.0     # ticker_perf_20d − spy_perf_20d ≥ 0pp (strict, no tolerance)
RS_INDEX_TICKER = "SPY5.DE"

# Partial-TP-Execution: bei TP1-Hit X% der Position schließen, Rest mit BE-SL + Trailing weiterlaufen.
# Wandelt Loser in BE-Trades nach 1R-Gewinnsicherung → Win-Rate-Bias.
PARTIAL_TP_FRACTION = 0.5         # 50% bei TP1 raus, 50% läuft weiter
AUTO_SPLIT_SINGLE_TP_AT_1R = True # Wenn nur 1 TP empfohlen: TP1 bei 1R einfügen, Original wird TP2

# Drawdown-Soft-Scaling: zwischen SOFT und HALT Stufe Risk-per-Trade halbieren.
# Behavioral Edge: kein Revenge-Trade nach Drawdown.
DRAWDOWN_SOFT_PERCENT = 4.0       # ab 4% Drawdown: Size *= 0.5 bis Equity neues High

# Korrelations-Gate: blockt Cluster-Risk auch quer durch Sektoren.
# 60d daily-return correlation zu existing holdings ≥ MAX_CORRELATION → Block.
CORRELATION_LOOKBACK_DAYS = 60
MAX_CORRELATION = 0.7
MAX_CORRELATED_HOLDINGS = 1       # max 1 hochkorrelierte Position erlaubt; ab 2 → Block

# Confluence-Score: deterministisches Setup-Quality-Scoring.
# Surface in Prompt, Sizing-konditional. Min-Score für Entry.
# 2026-05-12: 5→6 — strenger Quality-Filter. Bei R-Multiple 0.81 sind 5/10 Setups
# nicht überzeugend genug für Fee-Drag-Hürde. Mean-Reversion-Family kriegt
# weiterhin -2 Relax (siehe Gate 12) → de facto 4/10 für Reversion.
MIN_CONFLUENCE_SCORE = 6          # von 10 möglichen — darunter PASS

# Regime gate — RISK_OFF blocks new LONG entries (conservative full-trust bias)
RISK_OFF_BLOCKS_LONGS = True

# Red-Team-Pass: zweiter Claude-Call kritisiert eigene Rec im Bear-Modus.
# Blockt wenn confidence_thesis_holds < threshold ODER verdict==KILL.
# Gleicher Modell-Tier wie Hauptcall (Haiku Event/Opening, Sonnet Morning).
RED_TEAM_ENABLED = True
RED_TEAM_MIN_CONFIDENCE = 0.55  # confidence-of-thesis < 0.55 → block

# Setup-quality gate blocks → log+dashboard only, no Telegram (Audit 2026-04-27,
# RWE.DE triple-message bug). Only portfolio-wide safety pages the user.
GATE_BLOCK_NOTIFY_WHITELIST = {"risk_halt"}

# Stock-news pre-gate: skip Claude call when ticker has neither open position
# nor active morning watch_level — no thesis to verify, no position to manage,
# no actionable verdict possible. Set False to restore the CLAUDE.md "filter at
# output, not input" rule (every news headline goes to Claude).
NEWS_REQUIRE_OPEN_OR_WATCH = True

# ADD-tool invariants: pyramiding only allowed when (a) price still within X×ATR
# of original entry, (b) added size won't push position over MAX_POSITION_SIZE_PERCENT.
ADD_MAX_PRICE_DRIFT_ATR = 1.0

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
ANALYSIS_COST_EUR = 0.01          # ~cost per Haiku call (gemessen 2026-04, event/opening/news)

# Trade Republic constraint: SL nur auf ganze Stücke. Bruchstück-Position = SL-unmöglich =
# Verstoß gegen Full-Trust-SL-Invariant. Cap dynamisch aus total_capital × MAX_POSITION_SIZE_PERCENT.
# Helper: core.portfolio.max_affordable_share_price_eur(portfolio).
WHOLE_SHARE_PRICE_BUFFER = 1.0  # 1.0 = strikt; <1.0 = Slippage-Reserve (z.B. 0.95 = 5%)

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

# Geopolitische Trigger-Map für Alerts.
# Keywords werden mit \bkw\b matchen (siehe core/events._COMMODITY_TRIGGER_PATTERNS).
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

# Models: Sonnet für Morning-Brief (Senior-Reasoning, 1x/Tag), Haiku für Event-Checks (günstig, schnell)
CLAUDE_MODEL_MORNING = "claude-sonnet-4-6"
CLAUDE_MODEL_EVENT = "claude-haiku-4-5"
CLAUDE_MODEL = CLAUDE_MODEL_EVENT  # Default/Fallback für Standard-Mode
