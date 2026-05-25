"""Execution gates: SL/TP, slippage, no-entry zones, gate cooldowns, breakout/RS,
partial-TP, confluence, ADD-tool, whole-share buffer, notify whitelist."""

# 2026-05-07: 0.5→1.0 — see research/2026-05-07-haircut-cap-and-event-ttl.md
# Catches overshoot cases (3OIL.MI -20.9% intraday was 0.56% over trigger).
BREAKOUT_TRIGGER_PERCENT = 1.0    # Nähe zu Watch-Level (1.0% = approaching)

# 2026-05-07: see research/2026-05-07-haircut-cap-and-event-ttl.md
# Slack for tick/spread granularity (INL.DE 94.47 vs 94.48 confirm_close_above).
CONFIRM_CLOSE_TOLERANCE_PCT = 0.1

# 2026-05-07: see research/2026-05-07-haircut-cap-and-event-ttl.md
# TTL-based dedup replaced per-day so a hit dropped by downstream gate can
# re-fire after 60min cooldown (instead of blind for the rest of the day).
EVENT_DEDUP_TTL_MIN = 60

# 2026-05-17: see research/2026-05-17-entry-gate-cooldown.md
# RS / edge / red-team are intraday-stable; once failed, skip re-eval for 4h.
# Hard gate stays as backstop on live data (genuine improvements still pass).
ENTRY_GATE_COOLDOWN_MIN = 240     # 4h: kein erneuter Entry-Eval-Hinweis nach RS/edge/red-team-Fail

# 2026-05-04: see research/2026-05-13-exit-confirmation-gate.md
# One reminder, then auto-drop + cooldown. Prevents Claude re-issuing same exit
# minutes after user ignored it.
EXIT_AUTO_DROP_GAP_MIN = 60             # Pause zwischen Reminder und Auto-Drop
EXIT_REC_COOLDOWN_MIN_AFTER_DROP = 240  # 4h: keine neue Exit-Rec für Ticker nach Drop

# 2026-05-13: see research/2026-05-13-exit-confirmation-gate.md
# Thesis-decay exits need multi-signal confirmation (whipsaw protection on
# PUM.DE-style false-exits). Hard exits (SL/earnings/panic) bypass these.
EXIT_GATE_MIN_VOL_RATIO = 0.5      # Echter Selloff = vol_ratio ≥ 0.5; drunter = whipsaw
EXIT_GATE_MIN_RSI = 35             # RSI < 35 = oversold-Bounce-Zone → warten
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

# 2026-05-04: see research/2026-05-04-big-mover-bypass.md
# Big moves (≥4%) bypass cooldown so morning-prep doesn't swallow gap reactions.
BIG_MOVER_PCT_BYPASS = 4.0

# 2026-05-07: 1.3→1.0 — see research/2026-05-07-breakout-volume-floor.md
# XETRA mid-caps + early-day volume don't reach US-large-cap 1.3× threshold.
MIN_BREAKOUT_VOLUME_RATIO = 1.0   # heute_volume / avg_volume

# 2026-05-12: -1.0→0.0 — see research/2026-05-12-edge-floor-006.md
# Outperformer-only filter; mean-reversion family bypasses via SetupProfile.
MIN_RS_20D_VS_INDEX_PCT = 0.0     # ticker_perf_20d − spy_perf_20d ≥ 0pp
RS_INDEX_TICKER = "SPY5.DE"

# 2026-05-25: 0.5→0 — partial-TP disabled. At €1k capital with TR's €1/side
# fee, partial-sell at TP1 burned 25-50% of partial gross profit (extra fee
# event). New behavior: TP1-hit triggers BE+trail ONLY, no sell. Full position
# runs to TP2 or trail-stop. Risk-floor preserved (guaranteed ≥0 after BE-shift).
# Set >0 again to re-enable partials.
PARTIAL_TP_FRACTION = 0.0         # 0 = no partial, just BE+trail on TP1-hit

# Setup-types where final TP is a *trend-following signal* (let winners run via
# tightened trail) rather than a *reversal-target* (close at TP, lock profit).
# Swing-low family hits TP at resistance — typically reverses → close.
# Trend-family TPs are conservative milestones — often continues → lock+trail.
# 2026-05-25: introduced when removing partial-TP. Tightens trail to 1.0×ATR
# on lock-in (vs 1.5×ATR for TP1) so reversal catches faster after target hit.
TREND_FOLLOW_SETUPS = {
    "breakout_resistance",
    "flag_continuation",
    "earnings_drift",
    "pre_breakout_squeeze",
}
TRAIL_TIGHTEN_ATR_MULT_FINAL_TP = 1.0  # vs 1.5×ATR on TP1 lock-in

# 2026-05-12: 5→6 — see research/2026-05-12-edge-floor-006.md
# Deterministic setup-quality scoring; mean-reversion family relaxed -2 via SetupProfile.
MIN_CONFLUENCE_SCORE = 6          # von 10 möglichen — darunter PASS

# 2026-04-27: see research/2026-04-27-watch-self-sabotage.md
# Only portfolio-wide safety pages user; individual gate-blocks go to dashboard only.
GATE_BLOCK_NOTIFY_WHITELIST = {"risk_halt"}

# ADD-tool invariants: pyramiding only allowed when (a) price still within X×ATR
# of original entry, (b) added size won't push position over MAX_POSITION_SIZE_PERCENT.
ADD_MAX_PRICE_DRIFT_ATR = 1.0

# Trade Republic constraint: SL nur auf ganze Stücke. Bruchstück-Position = SL-unmöglich =
# Verstoß gegen Full-Trust-SL-Invariant. Cap dynamisch aus total_capital × MAX_POSITION_SIZE_PERCENT.
# Helper: core.portfolio.max_affordable_share_price_eur(portfolio).
WHOLE_SHARE_PRICE_BUFFER = 1.0  # 1.0 = strikt; <1.0 = Slippage-Reserve (z.B. 0.95 = 5%)
