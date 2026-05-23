"""Execution gates: SL/TP, slippage, no-entry zones, gate cooldowns, breakout/RS,
partial-TP, confluence, ADD-tool, whole-share buffer, notify whitelist."""

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

# 2026-05-17: Entry-Gate-Cooldown. RS-20d + edge sind intraday-stabil — failt ein
# Ticker eines dieser Gates, ist Re-Eval in derselben Stunde sinnlos (BAS.DE 05-15:
# 3× RS-Block in 2h). Cooldown markiert den Ticker im market_data-Dump, Claude
# überspringt ihn. Hard-Gate bleibt als Backstop auf Live-Daten — improved sich RS
# echt, lässt der Live-Check trotzdem durch (Cooldown ist Hinweis, kein Hard-Block).
ENTRY_GATE_COOLDOWN_MIN = 240     # 4h: kein erneuter Entry-Eval-Hinweis nach RS/edge-Fail

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
PENDING_REC_TTL_HOURS = 12        # Rec valide bis EOD — Sonnet morning rec mit Limit-Buy-Entry muss heute fillen
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
MIN_RS_20D_VS_INDEX_PCT = 0.0     # ticker_perf_20d − spy_perf_20d ≥ 0pp
RS_INDEX_TICKER = "SPY5.DE"

# Partial-TP-Execution: bei TP1-Hit X% der Position schließen, Rest mit BE-SL + Trailing weiterlaufen.
# Wandelt Loser in BE-Trades nach 1R-Gewinnsicherung → Win-Rate-Bias.
PARTIAL_TP_FRACTION = 0.5         # 50% bei TP1 raus, 50% läuft weiter

# Confluence-Score: deterministisches Setup-Quality-Scoring.
# Surface in Prompt, Sizing-konditional. Min-Score für Entry.
# 2026-05-12: 5→6 — strenger Quality-Filter. Bei R-Multiple 0.81 sind 5/10 Setups
# nicht überzeugend genug für Fee-Drag-Hürde. Mean-Reversion-Family kriegt
# weiterhin -2 Relax (siehe Gate 12) → de facto 4/10 für Reversion.
MIN_CONFLUENCE_SCORE = 6          # von 10 möglichen — darunter PASS

# Setup-quality gate blocks → log+dashboard only, no Telegram (Audit 2026-04-27,
# RWE.DE triple-message bug). Only portfolio-wide safety pages the user.
GATE_BLOCK_NOTIFY_WHITELIST = {"risk_halt"}

# ADD-tool invariants: pyramiding only allowed when (a) price still within X×ATR
# of original entry, (b) added size won't push position over MAX_POSITION_SIZE_PERCENT.
ADD_MAX_PRICE_DRIFT_ATR = 1.0

# Trade Republic constraint: SL nur auf ganze Stücke. Bruchstück-Position = SL-unmöglich =
# Verstoß gegen Full-Trust-SL-Invariant. Cap dynamisch aus total_capital × MAX_POSITION_SIZE_PERCENT.
# Helper: core.portfolio.max_affordable_share_price_eur(portfolio).
WHOLE_SHARE_PRICE_BUFFER = 1.0  # 1.0 = strikt; <1.0 = Slippage-Reserve (z.B. 0.95 = 5%)
