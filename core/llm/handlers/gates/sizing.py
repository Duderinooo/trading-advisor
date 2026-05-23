"""Sizing modifiers + final tradeability gates.

Modifiers (always pass, mutate entry):
  kelly_clamp:     fractional-Kelly cap on size_eur
  vix_dampener:    shrink size under elevated VIX
  dd_soft_scale:   half size between DD_SOFT and DD_HALT (behavioral edge)
  auto_split_tp:   single-TP → prepend 1R TP1 for partial scale-out

Final tradeability (may block):
  whole_share:     ≥1 whole share fits in size_eur (TR SL needs whole shares)
  fee:             gross @TP1 ≥ 2×fee + min_net_profit
"""

import logging

import config
from core.gate_log import log_gate
from core.portfolio import dd_scaling_factor, suggest_position_size
from core.llm.handlers.gates.context import GateContext


logger = logging.getLogger(__name__)


def gate_kelly_clamp(entry: dict, ctx: GateContext) -> bool:
    """Adaptive-Kelly clamp using Brier-derived kelly_mult. When calibration
    is poor (high Brier), shrinks Claude's size proposal toward fractional-Kelly
    on the noisy edge estimate. Mutates size_eur in-place + stamps kelly_clamp
    metadata."""
    p_win = entry.get("p_win")
    entry_p = float(entry.get("entry_price") or 0)
    sl = float(entry.get("stop_loss") or 0)
    tp = entry.get("take_profit")
    tp1 = tp[0] if isinstance(tp, list) and tp else (
        tp if isinstance(tp, (int, float)) else None
    )
    if not (isinstance(p_win, (int, float)) and 0 < p_win < 1
            and entry_p > sl > 0
            and isinstance(tp1, (int, float)) and tp1 > entry_p):
        return True
    r2r = (tp1 - entry_p) / (entry_p - sl)
    km = (ctx.stats or {}).get("kelly_mult") or config.KELLY_FRACTION
    atr_pct = ctx.snap_md.get("atr14_pct")
    kelly_cap = suggest_position_size(
        atr_pct, ctx.cash, p_win=p_win, reward_to_risk=r2r, kelly_mult=km,
    )
    orig_size = float(entry.get("size_eur") or 0)
    if orig_size > kelly_cap > 0:
        entry["size_eur"] = kelly_cap
        entry["kelly_clamp"] = {
            "kelly_mult": km, "r2r": round(r2r, 2),
            "original_size_eur": orig_size, "capped_size_eur": kelly_cap,
        }
        logger.warning(
            "Kelly-clamp: size €%.2f → €%.2f (kelly_mult=%.2f, p=%.2f, R:R=%.2f)",
            orig_size, kelly_cap, km, p_win, r2r,
        )
    return True


def gate_vix_dampener(entry: dict, ctx: GateContext) -> bool:
    """VIX size-dampening: shrink size under elevated/extreme volatility.
    Same risk-per-trade %, smaller notional so a fast move doesn't blow past SL intraday."""
    vix = (ctx.market_ctx.get("^VIX") or {}).get("price")
    factor = 1.0
    if isinstance(vix, (int, float)):
        if vix > 30:
            factor = 0.25
        elif vix > 20:
            factor = 0.5
    if factor >= 1.0:
        return True
    orig = float(entry.get("size_eur") or 0)
    if orig > 0:
        entry["size_eur"] = round(orig * factor, 2)
        entry["vix_dampener"] = {
            "vix": vix, "factor": factor, "original_size_eur": orig,
        }
        logger.warning(
            "VIX-dampener: size €%.2f → €%.2f (VIX=%.2f, factor=%.2f)",
            orig, entry["size_eur"], vix, factor,
        )
    return True


def gate_dd_soft_scale(entry: dict, ctx: GateContext) -> bool:
    """Drawdown-soft scaling: half size between DD_SOFT and DD_HALT thresholds.
    Behavioral edge: no revenge-trade after drawdown."""
    scale = dd_scaling_factor(ctx.portfolio)
    if scale >= 1.0:
        return True
    orig = float(entry.get("size_eur") or 0)
    if orig > 0:
        entry["size_eur"] = round(orig * scale, 2)
        entry["dd_soft_scale"] = {"factor": scale, "original_size_eur": orig}
        logger.warning(
            "DD-soft scaling: size €%.2f → €%.2f (factor=%.2f)",
            orig, entry["size_eur"], scale,
        )
    return True


def gate_auto_split_tp(entry: dict, ctx: GateContext) -> bool:
    """Auto-split single TP into [1R, original-TP] for partial scale-out.
    Enables 50%-Partial @ TP1 + Runner to TP2."""
    if not config.AUTO_SPLIT_SINGLE_TP_AT_1R:
        return True
    tp = entry.get("take_profit")
    e = float(entry.get("entry_price") or 0)
    s = float(entry.get("stop_loss") or 0)
    risk = e - s if (e > s > 0) else 0
    tp_list = tp if isinstance(tp, list) else ([tp] if tp else [])
    if not (len(tp_list) == 1 and risk > 0):
        return True
    tp1_new = round(e + risk, 2)
    tp2 = float(tp_list[0])
    if tp1_new < tp2:
        entry["take_profit"] = [tp1_new, tp2]
        entry["auto_split_tp"] = True
        logger.info(
            "Auto-split TP for partial scale-out: %s TP1=%.2f (1R) + TP2=%.2f (orig)",
            ctx.ticker, tp1_new, tp2,
        )
    return True


def gate_whole_share(entry: dict, ctx: GateContext) -> bool:
    """Whole-share hard-gate (runs AFTER all size-modifiers). ≥1 whole share
    must fit in size_eur — TR-SL only orders whole shares; Bruchstück-position
    = no SL on TR = violates Full-Trust-SL-Invariant."""
    e = float(entry.get("entry_price") or 0)
    size = float(entry.get("size_eur") or 0)
    whole = int(size / e) if e > 0 else 0
    if whole >= 1:
        return True
    frac = (size / e) if e > 0 else 0
    logger.warning(
        "Entry BLOCKED by whole_shares: %s €%.2f / size €%.2f = %.3f Stk (<1, no SL on TR)",
        ctx.ticker, e, size, frac,
    )
    log_gate(ctx.ticker, "whole_shares", True,
             f"price €{e:.2f} > size €{size:.2f} (only {frac:.3f} shares)",
             {"price": e, "size_eur": size, "whole_shares": round(frac, 3)})
    return False


def gate_fee(entry: dict, ctx: GateContext) -> bool:
    """Fixed-fee gate: TR €1/side × 2 = €2 roundtrip. Gross @TP1 on whole-shares
    must ≥ fees + MIN_NET_PROFIT_EUR; else trade is null-sum after costs.
    Conservative: uses TP1 not TP2 because auto-split partial closes 50% @ TP1
    (fee falls twice → gross only counts half)."""
    e = float(entry.get("entry_price") or 0)
    size = float(entry.get("size_eur") or 0)
    tp = entry.get("take_profit")
    tp1 = (
        float(tp[0]) if isinstance(tp, list) and tp
        else (float(tp) if isinstance(tp, (int, float)) else 0)
    )
    shares = int(size / e) if e > 0 else 0
    gross_profit = (tp1 - e) * shares if tp1 > e > 0 else 0
    fees_roundtrip = 2 * config.FIXED_FEE_EUR_PER_SIDE
    required = fees_roundtrip + config.MIN_NET_PROFIT_EUR
    if gross_profit >= required:
        return True
    logger.warning(
        "Entry BLOCKED by fee_gate: %s gross @TP1 €%.2f < required €%.2f "
        "(fees €%.2f + min_net €%.2f); shares=%d, TP1=%.2f, entry=%.2f",
        ctx.ticker, gross_profit, required, fees_roundtrip,
        config.MIN_NET_PROFIT_EUR, shares, tp1, e,
    )
    log_gate(ctx.ticker, "fee_gate", True,
             f"gross @TP1 €{gross_profit:.2f} < €{required:.2f}",
             {"gross_profit_eur": round(gross_profit, 2),
              "fees_roundtrip_eur": fees_roundtrip,
              "min_net_eur": config.MIN_NET_PROFIT_EUR,
              "shares": shares, "tp1": tp1, "entry": e})
    return False
