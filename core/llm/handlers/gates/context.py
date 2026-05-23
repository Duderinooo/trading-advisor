"""Shared context + pipeline orchestrator for entry-rec gates.

Each gate function takes (entry: dict, ctx: GateContext) and returns bool —
True = pass, False = block (gate logs reason itself). Modifier gates always
return True but mutate entry in-place (SL-clamp, Kelly-clamp, VIX-dampener,
DD-soft-scale, auto-split-TP).

run_entry_gates returns a structured DecisionResult capturing the full chain
(every gate visited, elapsed_ms per gate, blocked_by). The caller checks
result.passed and reads result.final_rec on pass.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

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


@dataclass(frozen=True)
class GateDecision:
    """One entry in the gate-chain trace."""
    gate: str        # gate function name, e.g. "gate_edge"
    passed: bool
    elapsed_ms: float


@dataclass
class DecisionResult:
    """Structured outcome of one entry-gate pipeline run.

    `decisions` records every gate visited (short-circuits on first block).
    `final_rec` is the mutated entry dict on full pass, None on block.
    `to_tree()` emits a human-readable audit path for /audit Telegram cmd."""
    ticker: str
    passed: bool
    blocked_by: str | None      # name of first blocking gate, None on full pass
    decisions: list[GateDecision] = field(default_factory=list)
    final_rec: dict | None = None
    timestamp: str = ""

    def to_tree(self) -> dict:
        """Compact audit-tree for dashboards / Telegram /audit."""
        return {
            "ticker": self.ticker,
            "decision": "PASS" if self.passed else "BLOCKED",
            "blocked_by": self.blocked_by,
            "timestamp": self.timestamp,
            "path": [
                f"{d.gate} -> {'PASS' if d.passed else 'FAIL'} ({d.elapsed_ms:.1f}ms)"
                for d in self.decisions
            ],
        }


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


def run_entry_gates(entry: dict, ctx: GateContext) -> DecisionResult:
    """Chain all entry-gate functions in canonical order.

    Returns a DecisionResult — caller checks `.passed`, reads `.final_rec` on
    pass. Order matters: validation first (catch malformed recs), then market-
    wide blocks, per-ticker quality, portfolio-cluster, sizing modifiers, final
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

    decisions: list[GateDecision] = []
    blocked_by: str | None = None
    for gate in chain:
        t0 = time.perf_counter()
        passed = gate(entry, ctx)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        decisions.append(GateDecision(
            gate=gate.__name__, passed=passed, elapsed_ms=round(elapsed_ms, 2),
        ))
        if not passed:
            blocked_by = gate.__name__
            _record_outcome_if_measurable(entry, ctx, gate.__name__)
            break

    return DecisionResult(
        ticker=ctx.ticker,
        passed=blocked_by is None,
        blocked_by=blocked_by,
        decisions=decisions,
        final_rec=entry if blocked_by is None else None,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


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
