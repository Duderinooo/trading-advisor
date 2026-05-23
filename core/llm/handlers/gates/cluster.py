"""Portfolio-cluster gates + red-team critic.

sector:       sector-position cap (e.g. ≥3 Semis long simultaneously = blocked).
correlation:  pairwise corr ≥ MAX_CORRELATION across ≥MAX_CORRELATED_HOLDINGS
              existing positions.
red_team:     Bear-critic Claude call. KILL or low-confidence → block + cooldown.
"""

import logging

import config
from core.gate_log import log_gate
from core.data.market_data import get_returns
from core.portfolio import (
    compute_correlations, compute_sector_exposure, record_entry_gate_cooldown,
)
from core.llm.handlers.gates.context import GateContext


logger = logging.getLogger(__name__)


def gate_sector(entry: dict, ctx: GateContext) -> bool:
    """Sector cluster cap. 3× Semis long simultaneously = one chip-crash hits
    three SLs. Diversification is the only free lunch. 'other' (unmapped) is
    not enforced."""
    sector = config.SECTOR_MAP.get(ctx.ticker)
    if not sector:
        return True
    exposure = compute_sector_exposure(ctx.portfolio)
    current = exposure.get(sector, [])
    if len(current) < config.MAX_POSITIONS_PER_SECTOR or ctx.ticker in current:
        return True
    logger.warning(
        "Entry BLOCKED by sector gate: %s in %s, already %d open (%s)",
        ctx.ticker, sector, len(current), ", ".join(current),
    )
    log_gate(ctx.ticker, "sector", True,
             f"{sector} {len(current)}/{config.MAX_POSITIONS_PER_SECTOR}",
             {"sector": sector, "current": current})
    return False


def gate_correlation(entry: dict, ctx: GateContext) -> bool:
    """Cluster-risk cross-sector. Block if ≥MAX_CORRELATED_HOLDINGS+1 existing
    positions show corr ≥ MAX_CORRELATION over CORRELATION_LOOKBACK_DAYS.
    Stamps corrs on rec for telemetry. Fail-soft: fetch errors don't block."""
    holdings = [
        tr.get("ticker") for tr in ctx.portfolio.get("open_trades", []) if tr.get("ticker")
    ]
    holdings = [h for h in holdings if h and h.upper() != ctx.ticker]
    if not holdings:
        return True
    try:
        returns = get_returns([ctx.ticker] + holdings, days=config.CORRELATION_LOOKBACK_DAYS)
        corrs = compute_correlations(returns, ctx.ticker)
        high = {h: c for h, c in corrs.items() if c >= config.MAX_CORRELATION}
        if len(high) > config.MAX_CORRELATED_HOLDINGS:
            logger.warning(
                "Entry BLOCKED by correlation gate: %s vs %s (corrs %s)",
                ctx.ticker, list(high.keys()), high,
            )
            log_gate(ctx.ticker, "correlation", True,
                     f"{len(high)} corr ≥ {config.MAX_CORRELATION}",
                     {"high_corrs": high})
            return False
        if corrs:
            entry["correlations"] = corrs
    except Exception as e:
        logger.warning("Correlation check failed for %s: %s", ctx.ticker, e)
    return True


def gate_red_team(entry: dict, ctx: GateContext) -> bool:
    """Red-team critic: bear-Claude reviews finished bull-rec. KILL or
    confidence < RED_TEAM_MIN_CONFIDENCE → block + intraday cooldown.
    WEAKEN passes through; critique stamped on rec for Telegram + dashboard.

    Cooldown via record_entry_gate_cooldown — a red-team verdict is intraday-
    stable. Without it, same ticker re-recommends within the hour (2026-05-19:
    CON.DE KILL 10:07 → re-rec 12:53)."""
    if not config.RED_TEAM_ENABLED:
        return True
    # Lazy import: tools.py imports gates package, gates would re-import tools.
    from core.llm.handlers.tools import run_red_team
    critique = run_red_team(entry, ctx.snap_md, ctx.regime, ctx.model)
    if not isinstance(critique, dict):
        return True
    verdict = (critique.get("verdict") or "").upper()
    conf = critique.get("confidence_thesis_holds")
    reason = critique.get("reason") or ""
    modes = critique.get("top_failure_modes") or []
    kill = (
        verdict == "KILL"
        or (isinstance(conf, (int, float)) and conf < config.RED_TEAM_MIN_CONFIDENCE)
    )
    if kill:
        logger.warning(
            "Entry BLOCKED by red-team: %s verdict=%s conf=%s reason=%s",
            ctx.ticker, verdict, conf, reason,
        )
        log_gate(ctx.ticker, "red_team", True,
                 f"verdict={verdict} conf={conf}",
                 {"verdict": verdict, "confidence": conf,
                  "failure_modes": modes, "reason": reason})
        record_entry_gate_cooldown(ctx.ticker, "red_team",
                                   f"verdict={verdict} conf={conf}")
        return False
    entry["red_team_review"] = {
        "verdict": verdict,
        "confidence_thesis_holds": conf,
        "top_failure_modes": modes,
        "reason": reason,
    }
    return True
