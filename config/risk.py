"""Risk management constants: capital, sizing, circuit breakers, edge gate, fees."""

# Trading capital (in EUR)
BUDGET_EUR = 1000.0

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
DRAWDOWN_SOFT_PERCENT = 4.0       # ab 4% Drawdown: Size *= 0.5 bis Equity neues High
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

# Min sample size for any threshold-tuning. Policy from CLAUDE.md "no tuning on N=1".
# Any constant in config/ that gets retuned should have ≥20 outcome samples
# justifying the change.
MIN_SAMPLE_SIZE_FOR_TUNING = 20

# TR-Fixkosten pro Order: €1 Kauf + €1 Verkauf = €2 Roundtrip. Bei kleinen Positionen
# (€80–100) frisst Fee einen merklichen Anteil am 1R-Gewinn → Gate fordert Brutto-
# Gewinn @ TP1 ≥ Fees + MIN_NET_PROFIT_EUR. Verhindert Null-Summen-Trades nach Kosten.
FIXED_FEE_EUR_PER_SIDE = 1.0      # Trade Republic Order-Gebühr pro Seite
MIN_NET_PROFIT_EUR = 2.0          # Mindest-Netto-Gewinn nach Fees am TP1

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
