"""Deterministic proposed-trade enrichment.

Turns a Sonnet proposal (ticker + setup_type + trigger/invalidate + thesis) into
a full, confirmable trade plan: entry, stop, TP ladder, whole-share size, fees,
R-multiples, and the gate verdicts. Single source of truth shared by the
dashboard proposal cards, `/confirm TICKER`, and the command-queue executor — so
all three show and lock identical numbers.

Role split (CLAUDE.md): Sonnet picks the setup + trigger (interpretation), this
module sizes + prices it (deterministic engine). No LLM here.

2026-06-16: introduced as the keystone of the proposed-trade workflow that
replaces the watch-level / event-entry chain. Three call sites: dashboard
proposal API, `/confirm TICKER`, command-queue executor.
"""

import config
from core.portfolio.sizing import suggest_position_size

# Default stop distance when the proposal carries no explicit invalidate level.
# 1.5×ATR sits inside the [MIN,MAX]_SL_DISTANCE_ATR band and matches the manual
# swing sizing used to hand-build cards before this module existed.
_DEFAULT_SL_ATR_MULT = 1.5

# TP ladder as R-multiples of the stop distance (deterministic; resistance-
# cluster refinement is Sonnet's job via the thesis, not the engine's).
_TP1_R = 2.0
_TP2_R = 3.0


def _live_price(snapshot: dict) -> float | None:
    for key in ("live_price", "price"):
        v = snapshot.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def enrich_proposed_trade(
    level: dict, snapshot: dict, cash: float, capital: float,
) -> dict | None:
    """Compute the full trade plan for one proposal.

    `level`    — Sonnet proposal (ticker, type, trigger_price, invalidate_below…).
    `snapshot` — that ticker's market_data row (price, atr14_pct, rsi14, …).
    `cash`/`capital` — portfolio cash + total capital for sizing.

    Returns a plan dict, or None when the snapshot lacks the price/ATR needed to
    size anything (caller drops the card). `shares` may be 0 with whole_share_ok
    False — the card still renders so the user can edit size up (play-money).
    """
    price = _live_price(snapshot)
    atr_pct = snapshot.get("atr14_pct")
    if price is None or not isinstance(atr_pct, (int, float)) or atr_pct <= 0:
        return None
    atr = price * atr_pct / 100.0

    # Entry: the proposal's trigger price is the limit-buy intent; fall back to
    # the zone midpoint, then live price.
    entry = level.get("trigger_price")
    if not isinstance(entry, (int, float)) or entry <= 0:
        zl, zh = level.get("zone_low"), level.get("zone_high")
        if isinstance(zl, (int, float)) and isinstance(zh, (int, float)):
            entry = (zl + zh) / 2.0
        else:
            entry = price
    entry = float(entry)

    # Stop: explicit invalidate if Sonnet gave a sane one (below entry), else a
    # default ATR stop. Then clamp the distance into the sanity band — too tight
    # widens to the floor, too wide tightens to the ceiling (display parity with
    # the SL-distance gate).
    inval = level.get("invalidate_below")
    if isinstance(inval, (int, float)) and 0 < inval < entry:
        sl = float(inval)
    else:
        sl = entry - _DEFAULT_SL_ATR_MULT * atr
    min_dist = config.MIN_SL_DISTANCE_ATR * atr
    max_dist = config.MAX_SL_DISTANCE_ATR * atr
    dist = entry - sl
    sl_clamped = None
    if dist < min_dist:
        sl, sl_clamped = entry - min_dist, "widened"
    elif dist > max_dist:
        sl, sl_clamped = entry - max_dist, "tightened"
    risk_per_share = entry - sl

    tp1 = entry + _TP1_R * risk_per_share
    tp2 = entry + _TP2_R * risk_per_share

    # Size: engine sizing capped by cash + position-size ceiling, floored to
    # whole shares (TR + SL invariant).
    size_eur = suggest_position_size(atr_pct, capital)
    pos_cap = capital * config.MAX_POSITION_SIZE_PERCENT / 100.0
    size_eur = min(size_eur, pos_cap, cash)
    shares = int(size_eur / entry) if entry > 0 else 0

    invest = shares * entry
    risk_eur = shares * risk_per_share
    fees = 2 * config.FIXED_FEE_EUR_PER_SIDE
    gross_tp1 = (tp1 - entry) * shares
    net_tp1 = gross_tp1 - fees
    gross_tp2 = (tp2 - entry) * shares

    fee_ok = gross_tp1 >= fees + config.MIN_NET_PROFIT_EUR
    whole_share_ok = shares >= 1
    affordable = entry <= config.MAX_SHARE_PRICE_EUR

    return {
        "ticker": level.get("ticker"),
        "setup_type": level.get("type") or level.get("setup_type"),
        "thesis": level.get("thesis"),
        "entry": round(entry, 2),
        "stop_loss": round(sl, 2),
        "sl_clamped": sl_clamped,
        "sl_dist_atr": round(risk_per_share / atr, 2) if atr else None,
        "take_profit": [round(tp1, 2), round(tp2, 2)],
        "tp_r": [_TP1_R, _TP2_R],
        "shares": shares,
        "invest_eur": round(invest, 2),
        "cash_left_eur": round(cash - invest, 2),
        "risk_eur": round(risk_eur, 2),
        "risk_pct_capital": round(risk_eur / capital * 100, 2) if capital else None,
        "fees_eur": round(fees, 2),
        "gross_tp1_eur": round(gross_tp1, 1),
        "net_tp1_eur": round(net_tp1, 1),
        "gross_tp2_eur": round(gross_tp2, 1),
        "gates": {
            "fee_ok": fee_ok,
            "whole_share_ok": whole_share_ok,
            "affordable": affordable,
        },
        # Setup signal passthrough for the card footer.
        "signals": {
            "base_quality_score": snapshot.get("base_quality_score"),
            "higher_lows_5d": snapshot.get("higher_lows_5d"),
            "rsi14": snapshot.get("rsi14"),
            "wk_trend": snapshot.get("wk_trend"),
            "rs_20d_vs_index_pct": snapshot.get("rs_20d_vs_index_pct"),
            "atr14_pct": atr_pct,
        },
    }
