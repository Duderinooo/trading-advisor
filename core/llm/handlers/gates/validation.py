"""Validation gates — schema + duplicate + Sonnet→Haiku coupling.

These run before any portfolio/market-data check. Cheap fail-fast filters that
catch truncated tool_use blocks, already-open re-entries, and event-mode entries
without a matching morning watch_level.
"""

import logging

from core.gate_log import log_gate
from core.llm.handlers.gates.context import GateContext


logger = logging.getLogger(__name__)


def gate_required_fields(entry: dict, ctx: GateContext) -> bool:
    """Reject truncated tool_use blocks. Required keys defined by the v7 schema.
    Bug 2026-04-30: SIE.DE + 3OIL.MI recs hit max_tokens=300, both missing
    setup_type + top_fail_mode, persisted as 'untagged' forever."""
    required = (
        "ticker", "entry_price", "stop_loss", "take_profit",
        "size_eur", "conviction", "p_win", "thesis",
        "setup_type", "top_fail_mode",
    )
    missing = [k for k in required if not entry.get(k)]
    if not missing:
        return True
    log_gate(
        (entry.get("ticker") or "?").upper(),
        "incomplete_rec", True,
        f"recommend_entry missing required fields: {','.join(missing)} — "
        f"likely max_tokens truncation",
        {"missing": missing, "received_keys": sorted(entry.keys())},
    )
    logger.error(
        "recommend_entry DROPPED: %s missing %s (truncated tool_use?)",
        entry.get("ticker"), missing,
    )
    return False


def gate_already_open(entry: dict, ctx: GateContext) -> bool:
    """Block re-entries on tickers we already hold. Pyramiding goes through
    recommend_add_to_position. Bug 2026-04-27: RWE.DE re-entry rec triggered
    news+entry+blocked triple-msg."""
    open_tickers = {
        (tr.get("ticker") or "").upper()
        for tr in ctx.portfolio.get("open_trades", [])
    }
    if ctx.ticker not in open_tickers:
        return True
    log_gate(
        ctx.ticker, "already_open", True,
        "ticker has open position — re-entry suppressed (use recommend_add_to_position)",
        {},
    )
    logger.info("Entry suppressed: %s already open (Claude should use recommend_add)", ctx.ticker)
    return False


def gate_event_watch_coupling(entry: dict, ctx: GateContext) -> bool:
    """Sonnet→Haiku coupling: event-mode (Haiku) may only enter tickers that have
    an active Morning watch_level (set by Sonnet). Verifies deterministic
    conditions; prevents mid-day improvised entries on 15min-delayed data.

    Stamps morning thesis on the rec so downstream alert + /confirm-snapshot
    carry the Sonnet-vetted thesis, not Haiku's."""
    if ctx.mode != "event":
        return True
    watch_match = next(
        (w for w in ctx.portfolio.get("watch_levels", [])
         if (w.get("ticker") or "").upper() == ctx.ticker),
        None,
    )
    if not watch_match:
        logger.warning(
            "Entry BLOCKED by event-watch-coupling: %s has no morning watch_level",
            ctx.ticker,
        )
        log_gate(
            ctx.ticker, "event_watch_coupling", True,
            "no morning watch_level for event-mode entry",
            {"ticker": ctx.ticker},
        )
        return False
    morning_thesis = watch_match.get("thesis")
    if morning_thesis:
        entry["watch_thesis"] = morning_thesis
    return True
