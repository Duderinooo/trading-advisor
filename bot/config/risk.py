"""Risk management constants: capital, sizing, circuit breakers, edge gate, fees."""

# Trading capital (in EUR)
BUDGET_EUR = 1000.0

# Risk management
MAX_RISK_PER_TRADE_PERCENT = 3.0  # Max % of capital to risk per trade

# 2026-05-12: 30→20 — see research/2026-05-12-edge-floor-006.md
# 2026-05-25: 20→35 — at €1k capital, 20% caps trades at €200 = fee-drag 25-50%
# on partial-TP. Bigger trades amortize TR's €1/side fixed fee better. Effective
# max parallel-trades drops from 5 to ~3 — acceptable for single-user play-money bot.
MAX_POSITION_SIZE_PERCENT = 35.0  # Sanity ceiling — Kelly/ATR/Conviction drive real size

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
DRAWDOWN_SOFT_PERCENT = 4.0       # ab 4% Drawdown: Size *= 0.5 bis Equity neues High
MAX_PORTFOLIO_HEAT_PERCENT = 10.0 # Max summed open risk (entry-SL) across all positions

# 2026-05-12: 0.04→0.06 — see research/2026-05-12-edge-floor-006.md
# Edge floor must account for €2 fixed-fee drag on €80–100 positions.
MIN_EXPECTED_EDGE = 0.06          # (p*b - (1-p)) ≥ 0.06 else force PASS
KELLY_FRACTION = 0.25             # Quarter-Kelly cap on size_pct

# 2026-05-13: see research/2026-05-13-brier-haircut-floor.md
# Brier-Haircut activates only above N=10 scored trades (below = noise).
MIN_CALIBRATION_N = 10            # Min scored Trades für Haircut-Aktivierung

# Min sample size for any threshold-tuning. Policy from CLAUDE.md "no tuning on N=1".
# Any constant in config/ that gets retuned should have ≥20 outcome samples
# justifying the change.
MIN_SAMPLE_SIZE_FOR_TUNING = 20

# TR-Fixkosten pro Order: €1 Kauf + €1 Verkauf = €2 Roundtrip. Bei kleinen Positionen
# (€80–100) frisst Fee einen merklichen Anteil am 1R-Gewinn → Gate fordert Brutto-
# Gewinn @ TP1 ≥ Fees + MIN_NET_PROFIT_EUR. Verhindert Null-Summen-Trades nach Kosten.
FIXED_FEE_EUR_PER_SIDE = 1.0      # Trade Republic Order-Gebühr pro Seite
# 2026-05-25: 2→4 — tighter fee-gate, ensures trades have meaningful net-profit
# headroom. Combines with sizing-cap raise (20→35%) + partial-TP removal.
MIN_NET_PROFIT_EUR = 4.0          # Mindest-Netto-Gewinn nach Fees am TP1

# Red-Team-Pass threshold (flag toggle in config/flags.py)
RED_TEAM_MIN_CONFIDENCE = 0.55    # confidence-of-thesis < 0.55 → block

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
