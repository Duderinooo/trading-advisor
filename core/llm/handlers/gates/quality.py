"""Per-ticker setup quality gates.

sl_distance:      MIN_SL_DISTANCE_ATR ≤ dist ≤ MAX. Too-tight clamps wider,
                  too-wide rejects (clamping tighter would stop before thesis-invalidation).
edge:             Brier-haircut-adjusted p_win, edge ≥ MIN_EXPECTED_EDGE.
weekly_trend:     no LONG vs wk_trend=DOWN.
earnings:         T-N to T+0 hard-block (override: setup_type=earnings_drift).
rs:               rs_20d_vs_index_pct ≥ floor (override: mean-rev family).
breakout_volume:  setup_type=breakout_resistance needs vol_ratio ≥ floor.
confluence:       deterministic score 0-10, mean-rev family relaxed by 2.
"""

import logging

import config
from core.gate_log import log_gate
from core.data.market_data import get_earnings_warnings
from core.portfolio import (
    compute_confluence, edge_ok, record_entry_gate_cooldown,
)
from core.llm.handlers.gates.context import GateContext


logger = logging.getLogger(__name__)


def gate_sl_distance(entry: dict, ctx: GateContext) -> bool:
    """SL-distance sanity. Tight (<MIN_SL_DISTANCE_ATR×ATR) → clamp wider.
    Wide (>MAX_SL_DISTANCE_ATR×ATR) → reject (would stop before thesis-invalidation)."""
    entry_p = float(entry.get("entry_price") or 0)
    sl = float(entry.get("stop_loss") or 0)
    atr = ctx.snap_md.get("atr14")
    if not (entry_p > sl > 0 and isinstance(atr, (int, float)) and atr > 0):
        return True
    sl_dist_atr = (entry_p - sl) / atr
    if sl_dist_atr < config.MIN_SL_DISTANCE_ATR:
        clamped_sl = round(entry_p - config.MIN_SL_DISTANCE_ATR * atr, 2)
        logger.warning(
            "SL clamped (too tight): %s %.2f→%.2f (%.2f×ATR → %.2f×ATR)",
            ctx.ticker, sl, clamped_sl, sl_dist_atr, config.MIN_SL_DISTANCE_ATR,
        )
        log_gate(ctx.ticker, "sl_distance", False,
                 f"SL clamped {sl_dist_atr:.2f}×ATR → {config.MIN_SL_DISTANCE_ATR}×ATR",
                 {"sl_dist_atr": round(sl_dist_atr, 2), "kind": "tight_clamped",
                  "sl_from": sl, "sl_to": clamped_sl})
        entry["stop_loss"] = clamped_sl
        return True
    if sl_dist_atr > config.MAX_SL_DISTANCE_ATR:
        logger.warning(
            "Entry BLOCKED by SL-too-wide: %s SL %.2f×ATR > %.2f×ATR",
            ctx.ticker, sl_dist_atr, config.MAX_SL_DISTANCE_ATR,
        )
        log_gate(ctx.ticker, "sl_distance", True,
                 f"SL {sl_dist_atr:.2f}×ATR > {config.MAX_SL_DISTANCE_ATR}",
                 {"sl_dist_atr": round(sl_dist_atr, 2), "kind": "wide"})
        return False
    return True


def gate_edge(entry: dict, ctx: GateContext) -> bool:
    """Edge gate with Brier-haircut. p_adj = p_raw − capped(haircut, ±0.20).
    See research/2026-05-07-haircut-cap-and-event-ttl.md for the cap rationale."""
    HAIRCUT_CAP = 0.20
    p_raw = entry.get("p_win")
    haircut = 0.0
    if ctx.stats and ctx.stats.get("calibration"):
        haircut = ctx.stats["calibration"].get("haircut") or 0.0
    if isinstance(p_raw, (int, float)) and haircut != 0:
        capped = max(-HAIRCUT_CAP, min(HAIRCUT_CAP, haircut))
        p_adj = max(0.01, min(0.99, p_raw - capped))
    else:
        p_adj = p_raw
    ok, edge = edge_ok(
        p_adj, entry.get("entry_price"), entry.get("stop_loss"), entry.get("take_profit"),
    )
    if ok:
        return True
    logger.warning(
        "Entry BLOCKED by edge gate: edge=%.3f < %.3f (p_raw=%s, haircut=%s, p_adj=%s)",
        edge, config.MIN_EXPECTED_EDGE, p_raw, haircut, p_adj,
    )
    log_gate(
        ctx.ticker, "edge", True,
        f"edge {edge:.3f} < {config.MIN_EXPECTED_EDGE}",
        {"edge": round(edge, 3), "p_raw": p_raw, "p_adj": p_adj, "haircut": haircut},
    )
    record_entry_gate_cooldown(ctx.ticker, "edge",
                               f"edge {edge:.3f} < {config.MIN_EXPECTED_EDGE}")
    return False


def gate_weekly_trend(entry: dict, ctx: GateContext) -> bool:
    """No LONG against weekly downtrend. CLAUDE.md rule, hard-enforced."""
    direction = str(entry.get("direction") or "LONG").upper()
    wk = ctx.snap_md.get("wk_trend")
    if not (direction == "LONG" and wk == "DOWN"):
        return True
    logger.warning("Entry BLOCKED by weekly-trend gate: %s LONG vs wk_trend=DOWN", ctx.ticker)
    log_gate(ctx.ticker, "weekly_trend", True, "LONG vs wk_trend=DOWN", {"wk_trend": wk})
    return False


def gate_earnings(entry: dict, ctx: GateContext) -> bool:
    """Earnings hard-block T-N to T+0. Gap-risk is coin-flip, no systematic edge.
    Override: setup_type=earnings_drift (post-earnings drift T+1+)."""
    setup = (entry.get("setup_type") or "").lower()
    if setup == "earnings_drift":
        return True
    ew = get_earnings_warnings([ctx.ticker], days_ahead=config.EARNINGS_ENTRY_BLOCK_DAYS)
    if not ew:
        return True
    w = ew[0]
    logger.warning(
        "Entry BLOCKED by earnings gate: %s in %d Tag(en) (%s)",
        ctx.ticker, w["days_until"], w["earnings_date"],
    )
    log_gate(ctx.ticker, "earnings", True,
             f"earnings in {w['days_until']}d",
             {"days_until": w["days_until"], "earnings_date": w["earnings_date"]})
    return False


def gate_rs(entry: dict, ctx: GateContext) -> bool:
    """Relative-Strength: no LONG on laggers vs uptrend. Override setups have
    RS-negative as part of the thesis (mean-rev / reversal / gap_fill / squeeze)."""
    setup = (entry.get("setup_type") or "").lower()
    override_setups = {
        "mean_reversion", "reversal_oversold", "gap_fill", "pre_breakout_squeeze",
    }
    if setup in override_setups:
        return True
    rs = ctx.snap_md.get("rs_20d_vs_index_pct")
    if not isinstance(rs, (int, float)):
        return True
    if rs >= config.MIN_RS_20D_VS_INDEX_PCT:
        return True
    logger.warning(
        "Entry BLOCKED by RS gate: %s rs_20d=%+.2fpp < %.2fpp (setup=%s)",
        ctx.ticker, rs, config.MIN_RS_20D_VS_INDEX_PCT, setup,
    )
    log_gate(ctx.ticker, "relative_strength", True,
             f"rs_20d {rs:+.1f}pp < {config.MIN_RS_20D_VS_INDEX_PCT}pp",
             {"rs_20d": rs, "setup": setup})
    record_entry_gate_cooldown(ctx.ticker, "relative_strength",
                               f"rs_20d {rs:+.1f}pp < {config.MIN_RS_20D_VS_INDEX_PCT}pp")
    return False


def gate_breakout_volume(entry: dict, ctx: GateContext) -> bool:
    """Breakout-resistance setups need vol_ratio ≥ MIN_BREAKOUT_VOLUME_RATIO to
    distinguish real breakout from fake (no-volume tag-and-fade)."""
    setup = (entry.get("setup_type") or "").lower()
    if setup != "breakout_resistance":
        return True
    vr = ctx.snap_md.get("volume_ratio")
    if not isinstance(vr, (int, float)) or vr >= config.MIN_BREAKOUT_VOLUME_RATIO:
        return True
    logger.warning(
        "Entry BLOCKED by volume gate: %s breakout vol_ratio=%.2f < %.2f",
        ctx.ticker, vr, config.MIN_BREAKOUT_VOLUME_RATIO,
    )
    log_gate(ctx.ticker, "breakout_volume", True,
             f"vol_ratio {vr:.2f} < {config.MIN_BREAKOUT_VOLUME_RATIO}",
             {"vol_ratio": vr})
    return False


def gate_confluence(entry: dict, ctx: GateContext) -> bool:
    """Deterministic confluence score 0-10 ≥ floor. Mean-rev family + squeeze
    relaxed by 2 (Antithese zur Trend-Confluence). Stamps score + items on rec."""
    setup = (entry.get("setup_type") or "").lower()
    snap = ctx.snap_md
    conf = compute_confluence(snap, ctx.regime) if snap else {
        "score": 0, "items": {}, "missing": ["no_data"],
    }
    min_conf = config.MIN_CONFLUENCE_SCORE
    if setup in ("mean_reversion", "reversal_oversold", "gap_fill", "pre_breakout_squeeze"):
        min_conf = max(3, config.MIN_CONFLUENCE_SCORE - 2)
    if conf["score"] < min_conf:
        logger.warning(
            "Entry BLOCKED by confluence gate: %s score=%d < %d (setup=%s, missing: %s)",
            ctx.ticker, conf["score"], min_conf, setup, ", ".join(conf.get("missing") or []),
        )
        log_gate(ctx.ticker, "confluence", True,
                 f"score {conf['score']}/10 < {min_conf}",
                 {"score": conf["score"], "min": min_conf,
                  "missing": conf.get("missing") or []})
        return False
    entry["confluence_score"] = conf["score"]
    entry["confluence_items"] = conf["items"]
    return True
