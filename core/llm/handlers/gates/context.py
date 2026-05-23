"""Shared context + pipeline orchestrator for entry-rec gates.

Each gate function takes (entry: dict, ctx: GateContext) and returns bool —
True = pass, False = block (gate logs reason itself). Modifier gates always
return True but mutate entry in-place (SL-clamp, Kelly-clamp, VIX-dampener,
DD-soft-scale, auto-split-TP).

run_entry_gates chains them in fixed order. Blocking gate returns None.
Full pass returns the (possibly mutated) entry dict.
"""

import logging
from dataclasses import dataclass

from core.portfolio import compute_hit_stats, load_portfolio


logger = logging.getLogger(__name__)


@dataclass
class GateContext:
    """Per-entry shared state across gate functions."""
    mode: str
    market_data: dict
    market_ctx: dict
    regime: str
    cash: float
    model: str
    portfolio: dict       # frozen pf_snapshot for the duration of the gate chain
    stats: dict | None    # compute_hit_stats result — None if <3 closed trades
    ticker: str           # entry['ticker'].upper()
    snap_md: dict         # market_data[ticker] or {}


def build_gate_context(
    entry: dict, *, mode: str, market_data: dict, market_ctx: dict,
    regime: str, cash: float, model: str,
) -> GateContext:
    """Build a GateContext for one entry-rec gate-chain run."""
    ticker = (entry.get("ticker") or "").upper()
    portfolio = load_portfolio()
    stats = compute_hit_stats(
        portfolio.get("closed_trades", []), portfolio.get("cash_movements", []),
    )
    return GateContext(
        mode=mode,
        market_data=market_data,
        market_ctx=market_ctx,
        regime=regime,
        cash=cash,
        model=model,
        portfolio=portfolio,
        stats=stats,
        ticker=ticker,
        snap_md=market_data.get(ticker) or {},
    )


def run_entry_gates(entry: dict, ctx: GateContext) -> dict | None:
    """Chain all entry-gate functions in canonical order.

    Returns the (possibly mutated) entry dict on full pass, None on first block.
    Order matters: validation first (catch malformed recs), then market-wide
    blocks, per-ticker quality, portfolio-cluster, sizing modifiers, final
    tradeability (whole-share + fee).
    """
    # Imports here (not module top) to avoid a circular import via
    # core.llm.handlers.gates.__init__ when gates pull from siblings.
    from core.llm.handlers.gates.validation import (
        gate_required_fields, gate_already_open, gate_event_watch_coupling,
    )
    from core.llm.handlers.gates.market import (
        gate_risk_halt, gate_regime, gate_no_entry_zone, gate_extended_up_day,
    )
    from core.llm.handlers.gates.quality import (
        gate_sl_distance, gate_edge, gate_weekly_trend, gate_earnings,
        gate_rs, gate_breakout_volume, gate_confluence,
    )
    from core.llm.handlers.gates.cluster import (
        gate_sector, gate_correlation, gate_red_team,
    )
    from core.llm.handlers.gates.sizing import (
        gate_kelly_clamp, gate_vix_dampener, gate_dd_soft_scale,
        gate_auto_split_tp, gate_whole_share, gate_fee,
    )

    chain = (
        # ---- Validation (no portfolio-snapshot dependency) ----
        gate_required_fields,
        gate_already_open,
        gate_event_watch_coupling,
        # ---- Market-wide risk blocks ----
        gate_risk_halt,
        gate_regime,
        gate_no_entry_zone,
        gate_extended_up_day,
        # ---- Per-ticker quality ----
        gate_sl_distance,        # may clamp SL or block on too-wide
        gate_edge,
        gate_sector,
        # ---- Sizing modifiers (pre-quality) ----
        gate_kelly_clamp,
        gate_vix_dampener,
        # ---- Remaining quality gates ----
        gate_weekly_trend,
        gate_earnings,
        gate_rs,
        gate_breakout_volume,
        gate_confluence,
        gate_correlation,
        gate_red_team,
        # ---- Remaining sizing modifiers ----
        gate_dd_soft_scale,
        gate_auto_split_tp,
        # ---- Final tradeability ----
        gate_whole_share,
        gate_fee,
    )

    for gate in chain:
        if not gate(entry, ctx):
            _record_outcome_if_measurable(entry, ctx, gate.__name__)
            return None
    return entry


def _record_outcome_if_measurable(entry: dict, ctx: GateContext, gate_name: str) -> None:
    """Capture blocked entry for next-day counterfactual measurement.
    Fail-soft: outcome-tracking must never block the trading flow."""
    try:
        from core.llm.telemetry.outcomes import record_blocked_entry
        record_blocked_entry(
            gate_name=gate_name,
            ticker=ctx.ticker,
            entry_price=entry.get("entry_price"),
            stop_loss=entry.get("stop_loss"),
            take_profit=entry.get("take_profit"),
            setup_type=entry.get("setup_type"),
            regime=ctx.regime,
        )
    except Exception:
        logger.exception("Outcome record failed for %s/%s", ctx.ticker, gate_name)
