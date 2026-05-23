"""Persist phase: lock-scoped write of recs / watch-levels / trace / metadata.

Reload-merge save protects against concurrent writes from telegram listener
(e.g. /confirm moving a pending_rec into open_trades).
"""

import logging
from datetime import datetime

import config
from core.gate_log import log_gate
from core.portfolio import (
    portfolio_lock, load_portfolio, save_portfolio,
    max_affordable_share_price_eur,
)
from core.llm.handlers.parser import Recs
from core.llm.prompt.context import RequestContext


logger = logging.getLogger(__name__)


def persist_results(
    ctx: RequestContext,
    *,
    recs: Recs,
    new_levels: list | None,
    corr_matrix: dict | None,
    trace: dict | None,
    trace_k: str | None,
) -> None:
    """Single lock-scoped write. recs is the Recs container from dispatch_tool_calls."""
    with portfolio_lock:
        fresh = load_portfolio()
        if corr_matrix is not None:
            fresh["correlation_matrix"] = corr_matrix
        if trace is not None and trace_k is not None:
            fresh[trace_k] = trace
        if new_levels is not None:
            _persist_watch_levels(
                fresh, new_levels, ctx.excluded, ctx.market_data, ctx.mode, trace,
            )
        if recs.entry is not None:
            fresh.setdefault("pending_recommendations", []).append(recs.entry)
        if recs.add is not None:
            fresh.setdefault("pending_recommendations", []).append(recs.add)
        if recs.update is not None:
            fresh.setdefault("pending_recommendations", []).append(recs.update)
        if recs.exit is not None:
            _persist_exit_with_cooldown_dedupe(fresh, recs.exit)
        fresh["last_analysis"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_portfolio(fresh)


def _persist_watch_levels(
    fresh: dict,
    new_levels: list,
    excluded: set,
    market_data: dict,
    mode: str,
    trace: dict | None,
) -> None:
    """Merge new watch-levels into portfolio. Filters: excluded tickers,
    self-sabotage resistance_reject (between entry and TP1), unaffordable
    (price > max-share-price). Empty new set keeps existing (no wipe)."""
    filtered = [lvl for lvl in new_levels if lvl.get("ticker") not in excluded]
    dropped = len(new_levels) - len(filtered)
    if dropped:
        logger.warning("Dropped %d watch level(s) on excluded tickers", dropped)
        if trace is not None:
            trace["dropped_excluded"] = dropped

    # Self-sabotage filter: drop resistance_reject between own entry and TP1.
    # (Bug 2026-04-27: RWE breakout @60.6 with TP1 62.4 + watch_reject @60.7 →
    # tag-and-dip read as exit while breakout was working.)
    _open_by_ticker = {t["ticker"]: t for t in fresh.get("open_trades", [])}
    _conflict_filtered = []
    _dropped_self_sabotage = 0
    for lvl in filtered:
        if lvl.get("type") != "resistance_reject":
            _conflict_filtered.append(lvl)
            continue
        _ot = _open_by_ticker.get(lvl.get("ticker"))
        if not _ot:
            _conflict_filtered.append(lvl)
            continue
        _entry = float(_ot.get("entry_price") or 0)
        _tp = _ot.get("take_profit")
        _tp1 = float(_tp[0]) if isinstance(_tp, list) and _tp else (
            float(_tp) if isinstance(_tp, (int, float)) else 0
        )
        _trig = float(lvl.get("trigger_price") or 0)
        if _entry > 0 and _tp1 > _entry and _entry < _trig <= _tp1:
            logger.warning(
                "Dropped self-sabotage watch_resistance_reject %s @%.2f "
                "(sits between entry %.2f and TP1 %.2f of open breakout)",
                lvl.get("ticker"), _trig, _entry, _tp1,
            )
            _dropped_self_sabotage += 1
            continue
        _conflict_filtered.append(lvl)
    filtered = _conflict_filtered
    if trace is not None and _dropped_self_sabotage:
        trace["dropped_self_sabotage"] = _dropped_self_sabotage

    # Whole-share filter on incoming levels.
    _max_share_price_lvl = max_affordable_share_price_eur(fresh)
    _kept_lvls = []
    _dropped_unaffordable_lvls = []
    for lvl in filtered:
        _lt = (lvl.get("ticker") or "").upper()
        _md = market_data.get(_lt) or {}
        _lp = _md.get("price")
        if not isinstance(_lp, (int, float)):
            _lp = lvl.get("current_price")
        if isinstance(_lp, (int, float)) and _lp > _max_share_price_lvl:
            _dropped_unaffordable_lvls.append(f"{_lt}(€{_lp:.2f})")
            continue
        _kept_lvls.append(lvl)
    if _dropped_unaffordable_lvls:
        logger.info("Watch-levels dropped (price>€%.2f): %s",
                    _max_share_price_lvl, ", ".join(_dropped_unaffordable_lvls))
        if trace is not None:
            trace["dropped_unaffordable"] = len(_dropped_unaffordable_lvls)
    filtered = _kept_lvls

    # Merge-by-ticker (empty new = keep existing). Bug 2026-04-28: Sonnet
    # returning [] used to WIPE all morning levels.
    existing = fresh.get("watch_levels", [])
    if not filtered:
        if mode == "morning":
            logger.error(
                "MORNING WATCHLEVEL FAIL: Sonnet returned 0 levels (existing=%d). "
                "Prompt requires ≥3. Check max_tokens / regime-defensiveness.",
                len(existing),
            )
        else:
            logger.info(
                "Watch levels: Sonnet returned 0 new levels — keeping %d existing",
                len(existing),
            )
        if trace is not None:
            trace["final_count"] = len(existing)
            trace["kept_existing"] = len(existing)
            trace["new_set"] = 0
            trace["final_tickers"] = [(lvl.get("ticker") or "?") for lvl in existing]
    else:
        new_tickers = {(lvl.get("ticker") or "").upper() for lvl in filtered}
        kept = [
            lvl for lvl in existing
            if (lvl.get("ticker") or "").upper() not in new_tickers
        ]
        merged = kept + filtered
        fresh["watch_levels"] = merged
        logger.info(
            "Watch levels merged: %d kept (other tickers) + %d new = %d total",
            len(kept), len(filtered), len(merged),
        )
        if trace is not None:
            trace["final_count"] = len(merged)
            trace["kept_existing"] = len(kept)
            trace["new_set"] = len(filtered)
            trace["final_tickers"] = [(lvl.get("ticker") or "?") for lvl in merged]


def _persist_exit_with_cooldown_dedupe(fresh: dict, exit_persisted: dict) -> None:
    """Two-stage suppress for exit-recs:
    (a) Cooldown: skip if trade had recent auto-dropped exit-rec (≤cooldown_min).
    (b) Keep-existing: if pending exit already exists for ticker, don't replace
        (replacing resets reminder timer and re-pings user)."""
    _et = (exit_persisted.get("ticker") or "").upper()
    _open_pos = next(
        (tr for tr in fresh.get("open_trades", []) or []
         if (tr.get("ticker") or "").upper() == _et),
        None,
    )
    if _open_pos and _open_pos.get("exit_dropped_at"):
        try:
            _drop_dt = datetime.strptime(
                _open_pos["exit_dropped_at"], "%Y-%m-%d %H:%M",
            )
            _age_min = (datetime.now() - _drop_dt).total_seconds() / 60.0
            if _age_min < config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP:
                log_gate(_et, "exit_cooldown", True,
                         f"in cooldown {_age_min:.0f}min < "
                         f"{config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP}min",
                         {"age_min": round(_age_min, 1),
                          "cooldown_min": config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP})
                logger.info(
                    "Exit-rec for %s suppressed (cooldown %.0fmin < %dmin)",
                    _et, _age_min, config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP,
                )
                return
        except ValueError:
            pass

    _existing = fresh.get("pending_recommendations", []) or []
    _has_pending = any(
        r.get("kind") == "exit"
        and (r.get("ticker") or "").upper() == _et
        for r in _existing
    )
    if _has_pending:
        log_gate(_et, "exit_dedupe", True,
                 "existing pending exit-rec, keep old (no timer reset)", {})
        logger.info("Exit-rec for %s suppressed (existing pending kept)", _et)
        return
    fresh.setdefault("pending_recommendations", []).append(exit_persisted)
