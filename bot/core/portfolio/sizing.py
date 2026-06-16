"""Position sizing: Kelly fraction + adaptive slippage budget + ATR risk + share-price cap.

suggest_position_size = central entry-size function, combines:
- ATR-based risk (entry × MAX_RISK_PER_TRADE × stop-distance)
- fractional-Kelly (per Brier-calibrated kelly_mult)
- hard caps (MAX_POSITION_SIZE_PERCENT × cash)
"""

import logging

import config


logger = logging.getLogger(__name__)


def compute_kelly_mult(closed_trades: list[dict]) -> float:
    """Adaptive Kelly fraction from rolling Brier-Score.

    Better calibration → bigger Kelly fraction. Worse → shrink. Range [0.10, 0.50].
    Until N≥10 scored trades exist, fall back to config.KELLY_FRACTION.

    Why: Kelly assumes edge estimate is correct. If our p_win is poorly calibrated,
    we overbet on bad estimates. Brier=0 perfect, 0.25 random — scale linearly.
    """
    scored = [
        t for t in closed_trades[-30:]
        if isinstance(t.get("brier"), (int, float))
    ]
    if len(scored) < 10:
        return config.KELLY_FRACTION
    avg_brier = sum(t["brier"] for t in scored) / len(scored)
    # Brier 0 → 0.50, Brier 0.25 (random) → 0.10. Linear interp.
    mult = 0.50 - (avg_brier / 0.25) * 0.40
    return max(0.10, min(0.50, round(mult, 2)))


def compute_slippage_budget(closed_trades: list[dict]) -> float:
    """Adaptive entry-slippage cap. Tightens if rolling avg slippage runs hot.

    Returns max-allowed slippage % (replaces static MAX_ENTRY_SLIPPAGE_PERCENT).
    Why: spread/liquidity drifts over time. Static gate either too lax (lets bad
    fills through) or too strict (blocks ok fills). Adaptive gate self-tunes.
    """
    recent = [
        abs(float(t.get("slippage_pct") or 0))
        for t in closed_trades[-30:]
        if t.get("slippage_pct") is not None
    ]
    base = config.MAX_ENTRY_SLIPPAGE_PERCENT
    if len(recent) < 10:
        return base
    avg = sum(recent) / len(recent)
    # If avg ≤0.3%: keep base. If avg ≥1.0%: clamp to 1.0%. Linear scale between.
    if avg <= 0.3:
        return base
    if avg >= 1.0:
        return 1.0
    return round(base - (avg - 0.3) / 0.7 * (base - 1.0), 2)


def max_affordable_share_price_eur(portfolio: dict) -> float:
    """Höchster Aktienpreis bei dem ≥1 ganzes Stück innerhalb der TR-SL-Order
    platzierbar ist.

    TR-Stop-Loss läuft nur auf ganze Stücke; Bruchstück-Position = SL-unmöglich =
    Verstoß gegen Full-Trust-SL-Invariant. Decoupled 2026-05-07 von Position-Size-
    Cap: jetzt eigener `MAX_SHARE_PRICE_EUR` (typisch €100), unabhängig davon
    wie groß die Position als Ganzes werden darf. Sizing-Decision (mehr/weniger
    Stücke) liegt beim Bot via Kelly/ATR/Conviction in suggest_position_size.

    `portfolio` parameter bleibt für API-Kompatibilität — wird derzeit nicht
    gelesen, aber Caller passen alle pf durch und falls wir zukünftig dynamisch
    werden (z.B. Cap relativ zu Cash) ist Hook da.
    """
    return float(config.MAX_SHARE_PRICE_EUR) * config.WHOLE_SHARE_PRICE_BUFFER


def suggest_position_size(
    atr14_pct: float | None,
    capital_eur: float,
    risk_pct: float = None,
    p_win: float | None = None,
    reward_to_risk: float | None = None,
    kelly_mult: float | None = None,
) -> float:
    """Position size: min(ATR-risk size, fractional-Kelly size, hard cap).

    ATR leg: capital × risk_pct / (1.5 × ATR%).
    Kelly leg (only if p_win + reward_to_risk given): f* = (p·b − (1−p))/b, scaled by kelly_mult.
    Hard cap: MAX_POSITION_SIZE_PERCENT of capital.

    `kelly_mult` defaults to config.KELLY_FRACTION but can be overridden with adaptive
    multiplier from `compute_kelly_mult(closed_trades)`.
    """
    if risk_pct is None:
        risk_pct = config.MAX_RISK_PER_TRADE_PERCENT
    if kelly_mult is None:
        kelly_mult = config.KELLY_FRACTION
    max_eur = capital_eur * config.MAX_POSITION_SIZE_PERCENT / 100

    if not atr14_pct or atr14_pct <= 0:
        atr_size = capital_eur * 0.10
    else:
        stop_dist_pct = atr14_pct * 1.5
        atr_size = (capital_eur * risk_pct / 100) / (stop_dist_pct / 100)

    size = atr_size
    if isinstance(p_win, (int, float)) and 0 < p_win < 1 and \
       isinstance(reward_to_risk, (int, float)) and reward_to_risk > 0:
        b = reward_to_risk
        f_kelly = (p_win * b - (1 - p_win)) / b
        if f_kelly > 0:
            kelly_eur = capital_eur * f_kelly * kelly_mult
            size = min(size, kelly_eur)
        else:
            size = 0.0  # negative edge → no trade

    size = min(size, max_eur)
    # Min-position floor: a positive-edge setup is floored UP to
    # MIN_POSITION_SIZE_PERCENT of capital so €1k positions clear the €2 fee
    # gate (quarter-Kelly otherwise sized ~€26 → every trade fee-blocked).
    # Negative-edge (size==0) stays 0 — no trade.
    if size > 0:
        floor_eur = capital_eur * config.MIN_POSITION_SIZE_PERCENT / 100
        size = min(max(size, floor_eur), max_eur)
    return round(size, 2)


# ---------- Circuit breakers ----------
