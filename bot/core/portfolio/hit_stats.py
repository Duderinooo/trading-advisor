"""Hit-rate + Brier calibration + per-setup expectancy + format.

compute_hit_stats (~330 LOC): aggregates closed_trades into:
- overall: total / win_rate / avg_r / avg_pnl_pct
- by_setup_type: per-setup breakdown
- by_regime: per-regime breakdown
- calibration: Brier score + p_predicted vs actual + haircut for edge gate
- class_suggestion: parameter tuning hint when mistake-class dominates

format_hit_stats: human-readable rendering for Telegram + morning prompt.
"""

import logging
from datetime import datetime, timedelta

import config
from core.portfolio.cash_movements import effective_pnl_eur, effective_pnl_pct
from core.portfolio.risk import _today_realized_pnl_eur
from core.portfolio.sizing import compute_kelly_mult


logger = logging.getLogger(__name__)


def compute_hit_stats(closed_trades: list[dict], cash_movements: list[dict] | None = None) -> dict | None:
    """Aggregate win-rate + R-multiple + conviction breakdown from closed trades.
    Returns None if not enough data (<3 closed trades).

    `cash_movements` (optional): dividends linked to trades fold into stats so
    RWE.DE -€13.50 trade + €7.20 div = -€6.30 effective. Omit = price-only (legacy).
    """
    if not closed_trades or len(closed_trades) < 3:
        return None

    movements = cash_movements or []

    def _eff_pct(t: dict) -> float:
        return effective_pnl_pct(t, movements) if movements else float(t.get("pnl_pct") or 0)

    def _eff_eur(t: dict) -> float:
        return effective_pnl_eur(t, movements) if movements else float(t.get("pnl_eur") or 0)

    # Exact-zero pnl is break-even, semantically neither win nor loss — exclude
    # from both buckets so avg_loss_pct + r_multiple aren't dragged toward 0.
    wins = [t for t in closed_trades if _eff_pct(t) > 0]
    losses = [t for t in closed_trades if _eff_pct(t) < 0]
    total = len(closed_trades)

    avg_win = sum(_eff_pct(t) for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(_eff_pct(t) for t in losses) / len(losses) if losses else 0.0
    r_multiple = (avg_win / abs(avg_loss)) if avg_loss else None

    by_conv: dict[int, list] = {}
    for t in closed_trades:
        c = t.get("conviction")
        if isinstance(c, (int, float)):
            by_conv.setdefault(int(c), []).append(t)
    conv_stats = {
        c: {
            "wins": sum(1 for t in ts if _eff_pct(t) > 0),
            "total": len(ts),
        }
        for c, ts in by_conv.items()
    }
    for c in conv_stats:
        s = conv_stats[c]
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0

    streak = "".join(
        "W" if _eff_pct(t) > 0 else "L"
        for t in closed_trades[-5:]
    )

    total_pnl_eur = round(sum(_eff_eur(t) for t in closed_trades), 2)

    # --- Brier-Score + Kalibrierung (rolling 20) ---
    # Nur Trades mit p_win-Prediction zählen. Ältere Trades ohne p_win werden ignoriert.
    scored = [
        t for t in closed_trades[-20:]
        if isinstance(t.get("p_win"), (int, float))
        and isinstance(t.get("brier"), (int, float))
    ]
    calibration: dict | None = None
    if scored:
        n = len(scored)
        avg_brier = sum(t["brier"] for t in scored) / n
        avg_p_pred = sum(t["p_win"] for t in scored) / n
        actual_win_rate = sum(t.get("outcome", 0) for t in scored) / n
        bias = avg_p_pred - actual_win_rate  # >0 = overconfident
        # Haircut nur aktiv ab MIN_CALIBRATION_N — drunter ist bias = Noise.
        # Stats werden trotzdem exposed (für Dashboard-Sichtbarkeit), aber Bot
        # zieht keine p_win-Korrektur aus n<10 ab.
        if n >= config.MIN_CALIBRATION_N:
            haircut = round(bias, 3) if abs(bias) >= 0.05 else 0.0
        else:
            haircut = 0.0
        calibration = {
            "n": n,
            "avg_brier": round(avg_brier, 4),
            "avg_p_predicted": round(avg_p_pred, 3),
            "actual_win_rate": round(actual_win_rate, 3),
            "bias": round(bias, 3),
            "haircut": haircut,
        }

    # --- Mistake-class distribution + actionable suggestion (last 20 losses) ---
    recent_losses = [t for t in closed_trades if _eff_pct(t) < 0][-20:]
    mistake_classes: dict[str, int] = {}
    for t in recent_losses:
        cls = t.get("mistake_class") or "untagged"
        mistake_classes[cls] = mistake_classes.get(cls, 0) + 1

    class_suggestion: str | None = None
    if recent_losses and len(recent_losses) >= 5:
        n_losses = len(recent_losses)
        # Ignore 'untagged' for dominance check (user may not have tagged yet).
        tagged = {c: n for c, n in mistake_classes.items() if c != "untagged"}
        if tagged:
            top_cls, top_n = max(tagged.items(), key=lambda x: x[1])
            share = top_n / n_losses
            if share >= 0.40:
                if top_cls == "execution":
                    class_suggestion = (
                        f"{top_cls} dominiert ({top_n}/{n_losses}={share:.0%}): "
                        f"MAX_SPREAD_PERCENT halbieren, Slippage-Limit strenger, "
                        f"keine Entries bei volume_ratio<0.8."
                    )
                elif top_cls == "timing":
                    class_suggestion = (
                        f"{top_cls} dominiert ({top_n}/{n_losses}={share:.0%}): "
                        f"Entry-Trigger strenger (Bestätigung auf 15m-Close statt Intraday), "
                        f"Breakout-Distance >1.0% statt 0.5%."
                    )
                elif top_cls == "prediction":
                    class_suggestion = (
                        f"{top_cls} dominiert ({top_n}/{n_losses}={share:.0%}): "
                        f"Conviction-Gate erhöhen (min 4/5 statt 3/5), "
                        f"p_win-Schwelle +0.05 (These muss stärker sein)."
                    )
                elif top_cls == "external":
                    class_suggestion = (
                        f"{top_cls} dominiert ({top_n}/{n_losses}={share:.0%}): "
                        f"Event-Kalender strikter (keine Entries 48h vor High-Impact), "
                        f"Position Size halbieren bei VIX>20."
                    )

    # --- Per-setup-type breakdown (which entry patterns work) ---
    by_setup: dict[str, dict] = {}
    for t in closed_trades:
        st = t.get("setup_type") or "untagged"
        by_setup.setdefault(st, {"wins": 0, "total": 0, "pnl_pct_sum": 0.0})
        by_setup[st]["total"] += 1
        if (t.get("pnl_pct") or 0) > 0:
            by_setup[st]["wins"] += 1
        by_setup[st]["pnl_pct_sum"] += (t.get("pnl_pct") or 0)
    for st, s in by_setup.items():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0
        s["avg_pnl_pct"] = round(s["pnl_pct_sum"] / s["total"], 2) if s["total"] else 0
        del s["pnl_pct_sum"]

    # --- Time-of-day / day-of-week bias on entry timestamp ---
    by_dow: dict[str, dict] = {}
    by_hour_bucket: dict[str, dict] = {}
    for t in closed_trades:
        ed = t.get("entry_date") or ""
        try:
            dt = datetime.strptime(ed, "%Y-%m-%d %H:%M")
        except Exception:
            continue
        dow = dt.strftime("%a")
        by_dow.setdefault(dow, {"wins": 0, "total": 0})
        by_dow[dow]["total"] += 1
        if (t.get("pnl_pct") or 0) > 0:
            by_dow[dow]["wins"] += 1

        # Hour buckets: open(09-10), morning(10-12), midday(12-15), us_open(15-17), late(17-22).
        h = dt.hour
        if 9 <= h < 10:
            bucket = "open"
        elif 10 <= h < 12:
            bucket = "morning"
        elif 12 <= h < 15:
            bucket = "midday"
        elif 15 <= h < 17:
            bucket = "us_open"
        else:
            bucket = "late"
        bucket_stats = by_hour_bucket.setdefault(
            bucket, {"wins": 0, "total": 0, "pnl_pct_sum": 0.0}
        )
        bucket_stats["total"] += 1
        bucket_stats["pnl_pct_sum"] += _eff_pct(t)
        if (t.get("pnl_pct") or 0) > 0:
            bucket_stats["wins"] += 1

    for k, s in by_dow.items():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0
    for k, s in by_hour_bucket.items():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0
        # avg PnL per bucket is the expectancy signal — win-rate alone
        # hides asymmetry (low-rate bucket can still be profitable if
        # the wins are large).
        s["avg_pnl_pct"] = round(s["pnl_pct_sum"] / s["total"], 2) if s["total"] else 0
        del s["pnl_pct_sum"]

    # --- Hold-duration buckets (entry → exit days) ---
    by_hold: dict[str, dict] = {}
    for t in closed_trades:
        ed = t.get("entry_date") or ""
        xd = t.get("exit_date") or ""
        try:
            d_in = datetime.strptime(ed, "%Y-%m-%d %H:%M")
            d_out = datetime.strptime(xd, "%Y-%m-%d %H:%M")
            held_days = max(0, (d_out - d_in).days)
        except Exception:
            continue
        if held_days <= 1:
            bucket = "0-1d"
        elif held_days <= 3:
            bucket = "2-3d"
        elif held_days <= 7:
            bucket = "4-7d"
        else:
            bucket = "8d+"
        by_hold.setdefault(bucket, {"wins": 0, "total": 0})
        by_hold[bucket]["total"] += 1
        if (t.get("pnl_pct") or 0) > 0:
            by_hold[bucket]["wins"] += 1
    for k, s in by_hold.items():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0

    # --- Regime × setup_type hit-rate (institutional: regime-conditional models) ---
    by_setup_regime: dict[str, dict] = {}
    for t in closed_trades:
        st = t.get("setup_type") or "untagged"
        rg = t.get("regime_at_entry") or "UNKNOWN"
        key = f"{st}@{rg}"
        by_setup_regime.setdefault(key, {"wins": 0, "total": 0})
        by_setup_regime[key]["total"] += 1
        if (t.get("pnl_pct") or 0) > 0:
            by_setup_regime[key]["wins"] += 1
    for k, s in by_setup_regime.items():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0

    # --- base_quality_score × outcome (validates swing-low philosophy) ---
    # Empirical test of the manifest claim "BQ ≥7 = A+ Swing-Low-Material". Buckets:
    #   strong   = BQ ≥ 7  (manifest's A+ swing-low threshold)
    #   building = 4 ≤ BQ ≤ 6 (base forming, half-size territory)
    #   no_base  = BQ < 4  (manifest says "no swing-low material")
    # Old trades without `base_quality_at_entry` are excluded — bucket only
    # accumulates as new trades close, so meaningful sample needs several closes
    # after the 2026-05-20 instrumentation. Lets us *validate* (not just assume)
    # whether high-BQ trades actually outperform low-BQ trades.
    by_base_quality: dict[str, dict] = {}
    for t in closed_trades:
        bq = t.get("base_quality_at_entry")
        if not isinstance(bq, (int, float)):
            continue
        if bq >= 7:
            bucket = "strong"
        elif bq >= 4:
            bucket = "building"
        else:
            bucket = "no_base"
        by_base_quality.setdefault(bucket, {"wins": 0, "total": 0, "pnl_pct_sum": 0.0})
        by_base_quality[bucket]["total"] += 1
        if _eff_pct(t) > 0:
            by_base_quality[bucket]["wins"] += 1
        by_base_quality[bucket]["pnl_pct_sum"] += _eff_pct(t)
    for bk, s in by_base_quality.items():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0
        s["avg_pnl_pct"] = round(s["pnl_pct_sum"] / s["total"], 2) if s["total"] else 0
        del s["pnl_pct_sum"]

    # --- Alpha vs Beta attribution (was loss skill or market noise?) ---
    attributed = [
        t for t in closed_trades
        if isinstance(t.get("alpha_pct"), (int, float))
    ]
    attribution: dict | None = None
    if len(attributed) >= 5:
        wins_alpha_pos = [t for t in attributed if (t.get("pnl_pct") or 0) > 0 and t["alpha_pct"] > 0]
        wins_alpha_neg = [t for t in attributed if (t.get("pnl_pct") or 0) > 0 and t["alpha_pct"] <= 0]
        loss_alpha_pos = [t for t in attributed if (t.get("pnl_pct") or 0) < 0 and t["alpha_pct"] > 0]
        loss_alpha_neg = [t for t in attributed if (t.get("pnl_pct") or 0) < 0 and t["alpha_pct"] <= 0]
        avg_alpha = sum(t["alpha_pct"] for t in attributed) / len(attributed)
        attribution = {
            "n": len(attributed),
            "avg_alpha_pct": round(avg_alpha, 2),
            "wins_with_alpha": len(wins_alpha_pos),       # real skill wins
            "wins_riding_market": len(wins_alpha_neg),    # lucky beta wins
            "losses_market_noise": len(loss_alpha_pos),   # lost despite beating market
            "losses_setup_fail": len(loss_alpha_neg),     # lost AND underperformed market
        }

    # --- Slippage tracking (drives adaptive gate) ---
    slip_recent = [
        abs(float(t.get("slippage_pct") or 0))
        for t in closed_trades[-30:]
        if t.get("slippage_pct") is not None
    ]
    slippage_stats: dict | None = None
    if len(slip_recent) >= 5:
        slippage_stats = {
            "n": len(slip_recent),
            "avg_pct": round(sum(slip_recent) / len(slip_recent), 3),
            "max_pct": round(max(slip_recent), 3),
            "current_budget_pct": compute_slippage_budget(closed_trades),
        }

    # --- Pre-mortem accuracy (did predicted top_fail_mode match reality?) ---
    fail_mode_to_class = {
        "support_breakdown": "prediction",
        "thesis_invalidation": "prediction",
        "earnings_miss": "external",
        "macro_event": "external",
        "regime_shift": "external",
        "sector_rotation": "external",
        "false_breakout": "timing",
        "stop_run": "timing",
    }
    premortem_losses = [
        t for t in closed_trades
        if (t.get("pnl_pct") or 0) < 0
        and t.get("top_fail_mode")
        and t.get("mistake_class")
    ]
    premortem_stats: dict | None = None
    if len(premortem_losses) >= 5:
        correct = sum(
            1 for t in premortem_losses
            if fail_mode_to_class.get(t["top_fail_mode"]) == t["mistake_class"]
        )
        premortem_stats = {
            "n": len(premortem_losses),
            "accuracy_pct": round(correct / len(premortem_losses) * 100, 1),
        }

    return {
        "total": total,
        "win_rate": round(len(wins) / total * 100, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "r_multiple": round(r_multiple, 2) if r_multiple else None,
        "total_pnl_eur": total_pnl_eur,
        "by_conviction": conv_stats,
        "by_setup": by_setup,
        "by_setup_regime": by_setup_regime,
        "by_base_quality": by_base_quality,
        "by_dow": by_dow,
        "by_hour": by_hour_bucket,
        "by_hold": by_hold,
        "recent_streak": streak,
        "calibration": calibration,
        "mistake_classes": mistake_classes,
        "class_suggestion": class_suggestion,
        "attribution": attribution,
        "slippage_stats": slippage_stats,
        "premortem_stats": premortem_stats,
        "kelly_mult": compute_kelly_mult(closed_trades),
    }


def format_hit_stats(stats: dict) -> str:
    if not stats:
        return ""
    r_str = f" | R {stats['r_multiple']}" if stats.get("r_multiple") else ""
    conv_line = " | ".join(
        f"Conv {c}/5: {s['rate']}% ({s['wins']}/{s['total']})"
        for c, s in sorted(stats.get("by_conviction", {}).items(), reverse=True)
    )
    lines = [
        f"{stats['total']} Trades | Win-Rate {stats['win_rate']}% | "
        f"Ø Win +{stats['avg_win_pct']}% | Ø Loss {stats['avg_loss_pct']}%{r_str} | "
        f"Total P&L €{stats['total_pnl_eur']:+.2f}"
    ]
    if conv_line:
        lines.append(conv_line)
    if stats.get("recent_streak"):
        lines.append(f"Last 5: {' '.join(stats['recent_streak'])}")
    cal = stats.get("calibration")
    if cal:
        direction = "overconfident" if cal["bias"] > 0 else "underconfident"
        line = (
            f"Brier (last {cal['n']}): {cal['avg_brier']} | "
            f"p_pred Ø {cal['avg_p_predicted']} vs tatsächlich {cal['actual_win_rate']} "
            f"→ {direction} um {abs(cal['bias']):.2f}"
        )
        if cal["haircut"]:
            # Haircut applied as `p_adj = p_raw - haircut`. Negative haircut →
            # under-confident → adjustment ADDS to p (be more aggressive).
            # Positive haircut → over-confident → adjustment SUBTRACTS (be stricter).
            hc = cal["haircut"]
            if hc > 0:
                action = f"sei STRENGER (Ziehe {hc:.2f} von neuen p_win ab — Bot war zu optimistisch)"
            else:
                action = f"sei AGGRESSIVER (Addiere {abs(hc):.2f} zu neuen p_win — Bot war zu pessimistisch)"
            line += (
                f" | KORREKTUR (auto-applied im edge gate, cap ±0.20): {action}"
            )
        lines.append(line)
    if stats.get("class_suggestion"):
        lines.append(f"🎯 SELBST-KALIBRIERUNG: {stats['class_suggestion']}")

    # Per-setup table (only show setups with ≥3 trades to avoid noise)
    setups = stats.get("by_setup") or {}
    setup_line = " | ".join(
        f"{name}: {s['rate']}% ({s['wins']}/{s['total']}, Ø{s['avg_pnl_pct']:+.1f}%)"
        for name, s in sorted(setups.items(), key=lambda x: -x[1]["total"])
        if s["total"] >= 3
    )
    if setup_line:
        lines.append(f"Setups: {setup_line}")

    # Day-of-week (only if at least one bucket ≥3)
    dows = stats.get("by_dow") or {}
    dow_line = " | ".join(
        f"{d}: {s['rate']}% ({s['wins']}/{s['total']})"
        for d, s in dows.items() if s["total"] >= 3
    )
    if dow_line:
        lines.append(f"DoW: {dow_line}")

    # Hour bucket (only if at least one bucket ≥3)
    hours = stats.get("by_hour") or {}
    hour_line = " | ".join(
        f"{h}: {s['rate']}% ({s['wins']}/{s['total']})"
        for h, s in hours.items() if s["total"] >= 3
    )
    if hour_line:
        lines.append(f"Entry-Zeit: {hour_line}")

    # Hold-duration
    holds = stats.get("by_hold") or {}
    hold_line = " | ".join(
        f"{b}: {s['rate']}% ({s['wins']}/{s['total']})"
        for b, s in sorted(holds.items()) if s["total"] >= 3
    )
    if hold_line:
        lines.append(f"Hold: {hold_line}")

    # Regime-conditional setups (only show buckets ≥3 — avoid noise from rare combos)
    sr = stats.get("by_setup_regime") or {}
    sr_line = " | ".join(
        f"{k}: {s['rate']}% ({s['wins']}/{s['total']})"
        for k, s in sorted(sr.items(), key=lambda x: -x[1]["total"])
        if s["total"] >= 3
    )
    if sr_line:
        lines.append(f"Setup×Regime: {sr_line}")

    # Alpha vs Beta attribution
    attr = stats.get("attribution")
    if attr:
        lines.append(
            f"Attribution (n={attr['n']}, Ø α {attr['avg_alpha_pct']:+.2f}%): "
            f"Wins skill {attr['wins_with_alpha']} / luck {attr['wins_riding_market']} | "
            f"Losses noise {attr['losses_market_noise']} / setup-fail {attr['losses_setup_fail']}"
        )

    # Slippage budget (adaptive)
    slip = stats.get("slippage_stats")
    if slip:
        lines.append(
            f"Slippage (last {slip['n']}): Ø {slip['avg_pct']}% | max {slip['max_pct']}% | "
            f"Budget aktuell: {slip['current_budget_pct']}%"
        )

    # Pre-mortem accuracy
    pm = stats.get("premortem_stats")
    if pm:
        lines.append(
            f"Pre-Mortem-Accuracy (last {pm['n']} losses): {pm['accuracy_pct']}% — "
            "predicted top_fail_mode entsprach realer mistake_class."
        )

    # Adaptive Kelly (only show if non-default)
    km = stats.get("kelly_mult")
    if isinstance(km, (int, float)) and km != config.KELLY_FRACTION:
        lines.append(f"Kelly-Mult: {km} (adaptiv aus Brier-Score)")

    return "\n".join(lines)


def compute_shadow_what_if(
    closed_trades: list[dict],
    shadow_overrides: dict[str, float],
) -> dict:
    """Counterfactual: for each closed trade with a recorded edge value,
    decide whether it would have been blocked under shadow MIN_EXPECTED_EDGE.

    Currently supports the single shadow key MIN_EXPECTED_EDGE — the
    only gate value we capture on the rec (via gate_edge stamping
    `expected_edge_at_entry`). Other shadow keys are recognised but
    skipped until their gate writes a similar trace.

    Returns:
        {
            'tested_count':           trades with the required trace
            'would_skip_count':       N that shadow would have blocked
            'would_skip_pnl_eur':     net PnL of blocked trades (positive
                                      = shadow misses profits; negative =
                                      shadow avoids losses)
            'kept_count':             N that pass under shadow too
            'kept_pnl_eur':           net PnL of pass-through trades
            'net_delta_eur':          kept_pnl_eur (i.e. what you'd have
                                      made under shadow); subtract
                                      original total to compare.
            'overrides':              the shadow overrides used
        }
    Empty dict if no trades carry the required trace.
    """
    if not shadow_overrides or not closed_trades:
        return {}
    shadow_edge = shadow_overrides.get("MIN_EXPECTED_EDGE")
    if shadow_edge is None:
        return {}

    tested = [
        t for t in closed_trades
        if isinstance(t.get("expected_edge_at_entry"), (int, float))
    ]
    if not tested:
        return {}

    would_skip = [t for t in tested if t["expected_edge_at_entry"] < shadow_edge]
    kept = [t for t in tested if t["expected_edge_at_entry"] >= shadow_edge]
    return {
        "tested_count": len(tested),
        "would_skip_count": len(would_skip),
        "would_skip_pnl_eur": round(
            sum(float(t.get("pnl_eur") or 0) for t in would_skip), 2
        ),
        "kept_count": len(kept),
        "kept_pnl_eur": round(
            sum(float(t.get("pnl_eur") or 0) for t in kept), 2
        ),
        "net_delta_eur": round(
            sum(float(t.get("pnl_eur") or 0) for t in kept), 2
        ),
        "overrides": dict(shadow_overrides),
    }


def compute_hit_rate_trend(
    closed_trades: list[dict],
    window_days: int = 30,
    step_days: int = 1,
) -> list[dict]:
    """Rolling N-day hit-rate over time, anchored on trade exit_date.

    For each step_days-spaced point in the last `total span` of activity,
    look back window_days and compute win-rate + avg pnl % across trades
    that closed in that window. Lets the dashboard plot a sparkline so
    the user can see whether the learning loop is improving the strategy.

    Returns list of {'date', 'n', 'win_rate', 'avg_pnl_pct'} oldest-first.
    Empty list if not enough trades to span at least one window.
    """
    if not closed_trades:
        return []

    parsed: list[tuple[datetime, dict]] = []
    for t in closed_trades:
        ed = t.get("exit_date") or ""
        try:
            parsed.append((datetime.strptime(ed[:10], "%Y-%m-%d"), t))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return []

    parsed.sort(key=lambda p: p[0])
    first_dt = parsed[0][0]
    last_dt = parsed[-1][0]
    if (last_dt - first_dt).days < window_days:
        # Not enough span — one summary point is misleading as a trend.
        return []

    out: list[dict] = []
    cursor = first_dt + timedelta(days=window_days)
    step = timedelta(days=step_days)
    while cursor <= last_dt:
        win_start = cursor - timedelta(days=window_days)
        bucket = [t for dt, t in parsed if win_start <= dt <= cursor]
        if bucket:
            wins = sum(1 for t in bucket if (t.get("pnl_pct") or 0) > 0)
            avg_pnl = sum(float(t.get("pnl_pct") or 0) for t in bucket) / len(bucket)
            out.append({
                "date": cursor.strftime("%Y-%m-%d"),
                "n": len(bucket),
                "win_rate": round(wins / len(bucket) * 100, 1),
                "avg_pnl_pct": round(avg_pnl, 2),
            })
        cursor += step
    return out


# ---------- Portfolio heat (risk-sizing guardrail) ----------
