"""End-of-day + Weekend summary services. Deterministic, no Claude calls."""

import logging
from datetime import date, datetime, timedelta

import config
from core import (
    load_portfolio, get_market_data, kill_switch_active, get_daily_usage,
    compute_portfolio_heat, compute_hit_stats, compute_equity_stats,
    get_earnings_warnings,
)
from macro import today_events as _macro_today
from notifier import send_daily_summary, send_notification

from runtime.scheduler import (
    eod_summary_done_today, mark_eod_summary_done,
    weekend_summary_done_today, mark_weekend_summary_done,
)


logger = logging.getLogger("trading_advisor.summary")


def run_eod_summary() -> None:
    """One-shot EOD digest after US close."""
    if eod_summary_done_today():
        return
    try:
        portfolio = load_portfolio()
        today = str(date.today())

        closed_today = [
            t for t in portfolio.get("closed_trades", [])
            if (t.get("exit_date") or "").startswith(today)
        ]
        realized_eur = sum(float(t.get("pnl_eur") or 0) for t in closed_today)
        wins_today = sum(1 for t in closed_today if (t.get("pnl_eur") or 0) > 0)

        open_trades = portfolio.get("open_trades", [])
        unrealized_eur = 0.0
        if open_trades:
            try:
                live = get_market_data([t["ticker"] for t in open_trades])
                for t in open_trades:
                    snap = live.get(t["ticker"])
                    price = snap.get("price") if isinstance(snap, dict) else None
                    entry = float(t.get("entry_price") or 0)
                    shares = float(t.get("shares") or 0)
                    if isinstance(price, (int, float)) and entry > 0:
                        unrealized_eur += (price - entry) * shares
            except Exception:
                logger.exception("EOD live-pull failed")

        calls_today = get_daily_usage()
        ks_state = "🛑 AKTIV" if kill_switch_active(portfolio) else "✅ aus"
        dd_state = "🚫 DD-LATCH" if portfolio.get("dd_halt_active") else "—"

        cash = float(portfolio.get("cash_eur") or 0)
        starting = float(portfolio.get("total_capital_eur") or config.BUDGET_EUR)
        equity_realized = starting + sum(float(t.get("pnl_eur") or 0) for t in portfolio.get("closed_trades", []))
        equity_total = equity_realized + unrealized_eur

        msg = (
            f"📊 *EOD {today}*\n\n"
            f"Realized today: €{realized_eur:+.2f} ({len(closed_today)} closed, {wins_today}W)\n"
            f"Unrealized: €{unrealized_eur:+.2f} ({len(open_trades)} offen)\n"
            f"Cash: €{cash:.2f} | Equity: €{equity_total:.2f} (Start €{starting:.2f})\n\n"
            f"Claude calls: {calls_today}/{config.MAX_ANALYSES_PER_DAY}\n"
            f"Kill-Switch: {ks_state} | DD-Halt: {dd_state}"
        )
        send_notification(msg)
        _persist_eod_analytics_snapshots(portfolio)
        _run_db_backup()
        mark_eod_summary_done()
        logger.info("✅ EOD summary sent")
    except Exception:
        logger.exception("EOD summary failed")


def _persist_eod_analytics_snapshots(portfolio: dict) -> None:
    """Resolve yesterday's blocked-entry counterfactuals + persist a daily
    snapshot of setup-expectancy + calibration. Fail-soft: any analytics error
    must not break the EOD summary."""
    try:
        from core.llm.telemetry.outcomes import (
            compute_pending_outcomes, gate_false_negative_rates,
        )
        summary = compute_pending_outcomes()
        if summary["resolved"]:
            logger.info(
                "EOD outcomes: %d resolved (%d would-have-won), %d still pending",
                summary["resolved"], summary["false_neg_count"], summary["remaining"],
            )
            fnr = gate_false_negative_rates()
            if fnr:
                logger.info("Gate false-negative rates: %s", fnr)
    except Exception:
        logger.exception("EOD outcomes resolution failed")

    try:
        _append_expectancy_snapshot(portfolio)
    except Exception:
        logger.exception("EOD expectancy snapshot failed")

    try:
        _append_calibration_snapshot(portfolio)
    except Exception:
        logger.exception("EOD calibration snapshot failed")


def _run_db_backup() -> None:
    """Snapshot state/bot.db once per day at EOD. Fail-soft."""
    try:
        from tools.backup_db import prune, snapshot
        out = snapshot()
        removed = prune()
        logger.info(
            "EOD db backup: wrote %s (%.1f KB), pruned %d old",
            out.name, out.stat().st_size / 1024, removed,
        )
    except Exception:
        logger.exception("EOD db backup failed")

    # JSONL rotation runs alongside the DB snapshot: same daily cadence,
    # same fail-soft envelope.
    try:
        from tools.rotate_jsonl import rotate_all
        results = rotate_all()
        trimmed = {k: v for k, v in results.items() if v[0] != v[1]}
        if trimmed:
            logger.info("EOD jsonl rotation: %s",
                        ", ".join(f"{k}:{a}->{b}" for k, (a, b) in trimmed.items()))
    except Exception:
        logger.exception("EOD jsonl rotation failed")

    try:
        refresh_earnings_calendar()
    except Exception:
        logger.exception("EOD earnings calendar refresh failed")

    try:
        refresh_analytics()
    except Exception:
        logger.exception("EOD analytics refresh failed")


def refresh_analytics() -> None:
    """Recompute the dashboard-facing analytics snapshots and persist
    them to kv_state(namespace='analytics'). Reads + writes happen here
    so the dashboard sees a stable Python-computed view instead of a
    half-ported TS reimplementation. Called from EOD + morning prep +
    every confirm/close handler so widgets are always fresh."""
    from core.portfolio import load_portfolio
    from core.portfolio.hit_stats import (
        compute_hit_rate_trend, compute_hit_stats, compute_shadow_what_if,
    )
    from core.portfolio.risk import compute_drawdown_trajectory
    from core.db import connect, init_schema
    from datetime import datetime as _dt
    import json as _json

    portfolio = load_portfolio()
    closed = portfolio.get("closed_trades") or []

    stats = compute_hit_stats(closed)
    payloads = {
        "drawdown_trajectory": compute_drawdown_trajectory(portfolio),
        "hit_rate_trend": compute_hit_rate_trend(closed),
        "time_of_day": (stats or {}).get("by_hour") or {},
        "shadow_what_if": compute_shadow_what_if(closed, config.SHADOW_OVERRIDES),
    }
    generated_at = _dt.now().strftime("%Y-%m-%d %H:%M")

    init_schema()
    with connect() as conn:
        for key, body in payloads.items():
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (namespace, key, body) "
                "VALUES (?, ?, ?)",
                (
                    "analytics", key,
                    _json.dumps({"generated_at": generated_at, "data": body}),
                ),
            )


def refresh_earnings_calendar(days_ahead: int = 14) -> dict:
    """Persist a 14-day earnings calendar for watchlist + open positions
    into kv_state(namespace='calendar', key='earnings'). Dashboard reads
    from there so the widget can render without hitting yfinance live."""
    from core.data.market_data import get_earnings_warnings
    from core.db import connect, init_schema
    from datetime import datetime as _dt
    import json as _json

    portfolio = load_portfolio()
    tickers = list(dict.fromkeys(
        [t.get("ticker") for t in portfolio.get("open_trades", []) or [] if t.get("ticker")]
        + list(config.WATCHLIST or [])
    ))
    warnings = get_earnings_warnings(tickers, days_ahead=days_ahead)
    payload = {
        "generated_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
        "days_ahead": days_ahead,
        "events": warnings,
    }
    init_schema()
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (namespace, key, body) "
            "VALUES (?, ?, ?)",
            ("calendar", "earnings", _json.dumps(payload)),
        )
    logger.info("Earnings calendar refreshed: %d events over %dd",
                len(warnings), days_ahead)
    return payload


def _append_expectancy_snapshot(portfolio: dict) -> None:
    """Append today's setup-expectancy + calibration metrics to
    analytics/setup_expectancy_history.jsonl. Daily resolution; cheap rolling
    snapshot for tuning audits."""
    import json
    import os
    from pathlib import Path
    from core.portfolio import compute_hit_stats

    stats = compute_hit_stats(
        portfolio.get("closed_trades", []), portfolio.get("cash_movements", []),
    )
    if not stats:
        return
    snapshot = {
        "date": str(date.today()),
        "n_trades": stats.get("total"),
        "win_rate": stats.get("win_rate"),
        "avg_r": stats.get("avg_r"),
        "by_setup_type": stats.get("by_setup_type"),
        "by_regime": stats.get("by_regime"),
        "calibration": stats.get("calibration"),
        "kelly_mult": stats.get("kelly_mult"),
    }
    analytics_dir = (
        Path(os.path.dirname(os.path.abspath(__file__))) / ".." / "analytics"
    ).resolve()
    analytics_dir.mkdir(parents=True, exist_ok=True)
    out_path = analytics_dir / "setup_expectancy_history.jsonl"
    with out_path.open("a") as f:
        f.write(json.dumps(snapshot) + "\n")


def _append_calibration_snapshot(portfolio: dict) -> None:
    """Append today's rolling-N Brier-score calibration to
    analytics/calibration_history.jsonl. Lighter than expectancy snapshot —
    only the calibration block. Used for drift detection over time."""
    import json
    import os
    from pathlib import Path
    from core.portfolio import compute_hit_stats

    stats = compute_hit_stats(
        portfolio.get("closed_trades", []), portfolio.get("cash_movements", []),
    )
    cal = (stats or {}).get("calibration") or {}
    if not cal:
        return
    snapshot = {
        "date": str(date.today()),
        "n_scored": cal.get("n_scored"),
        "avg_brier": cal.get("avg_brier"),
        "avg_p_predicted": cal.get("avg_p_predicted"),
        "actual_win_rate": cal.get("actual_win_rate"),
        "haircut": cal.get("haircut"),
    }
    analytics_dir = (
        Path(os.path.dirname(os.path.abspath(__file__))) / ".." / "analytics"
    ).resolve()
    analytics_dir.mkdir(parents=True, exist_ok=True)
    out_path = analytics_dir / "calibration_history.jsonl"
    with out_path.open("a") as f:
        f.write(json.dumps(snapshot) + "\n")


def run_weekend_summary() -> None:
    """Sat/Sun 10:00 CET digest. Deterministic, no Claude."""
    if weekend_summary_done_today():
        return
    try:
        portfolio = load_portfolio()
        today = date.today()

        cutoff = today - timedelta(days=7)
        closed_recent = [
            t for t in portfolio.get("closed_trades", [])
            if (t.get("exit_date") or "") >= str(cutoff)
        ]
        realized_eur = sum(float(t.get("pnl_eur") or 0) for t in closed_recent)
        wins = sum(1 for t in closed_recent if (t.get("pnl_eur") or 0) > 0)
        losses = len(closed_recent) - wins

        open_trades = portfolio.get("open_trades", [])
        unrealized = 0.0
        if open_trades:
            try:
                live = get_market_data([t["ticker"] for t in open_trades])
                for t in open_trades:
                    snap = live.get(t["ticker"])
                    price = snap.get("price") if isinstance(snap, dict) else None
                    entry = float(t.get("entry_price") or 0)
                    shares = float(t.get("shares") or 0)
                    if isinstance(price, (int, float)) and entry > 0:
                        unrealized += (price - entry) * shares
            except Exception:
                logger.exception("Weekend live-pull failed")

        starting = float(portfolio.get("total_capital_eur") or config.BUDGET_EUR)
        equity_realized = (
            starting
            + sum(float(t.get("pnl_eur") or 0) for t in portfolio.get("closed_trades", []))
            + sum(float(m.get("amount") or 0) for m in portfolio.get("cash_movements", []))
        )
        equity_total = equity_realized + unrealized

        heat = compute_portfolio_heat(portfolio)
        eq = compute_equity_stats(
            portfolio.get("closed_trades", []),
            starting,
            portfolio.get("cash_movements", []),
        ) or {}
        stats = compute_hit_stats(portfolio.get("closed_trades", []), portfolio.get("cash_movements", []))

        last_losses = [
            t for t in portfolio.get("closed_trades", [])
            if (t.get("pnl_pct") or 0) < 0
        ][-20:]
        mistake_line = ""
        if last_losses:
            cls_counts: dict[str, int] = {}
            for t in last_losses:
                c = t.get("mistake_class") or "untagged"
                cls_counts[c] = cls_counts.get(c, 0) + 1
            mistake_line = " | ".join(f"{c}={n}" for c, n in sorted(cls_counts.items(), key=lambda x: -x[1]))

        candidates = list({t["ticker"] for t in open_trades} | set(config.WATCHLIST))
        upcoming_earn = get_earnings_warnings(candidates, days_ahead=7)
        earn_line = ""
        if upcoming_earn:
            earn_line = "\n".join(
                f"  • {w['ticker']}: T-{w['days_until']} ({w['earnings_date']})"
                for w in sorted(upcoming_earn, key=lambda x: x["days_until"])[:8]
            )

        macro = _macro_today()
        macro_line = ""
        if macro:
            macro_line = "\n".join(
                f"  • {m.get('time','?')} {m.get('country','')}: {m.get('event','?')}"
                for m in macro[:5]
            )

        ks_state = "🛑 AKTIV" if kill_switch_active(portfolio) else "✅ aus"
        dd_state = "🚫 DD-LATCH" if portfolio.get("dd_halt_active") else "—"
        calls_today = get_daily_usage()

        msg = (
            f"📅 *WEEKEND-RECAP {today}*\n\n"
            f"_Last 7d_: €{realized_eur:+.2f} ({len(closed_recent)} closed, {wins}W/{losses}L)\n"
            f"Unrealized: €{unrealized:+.2f} ({len(open_trades)} offen)\n"
            f"Equity: €{equity_total:.2f} | Cash: €{portfolio.get('cash_eur',0):.2f}\n"
            f"Max DD all-time: {eq.get('max_drawdown_pct',0):.1f}% | "
            f"Heat: €{heat['total_heat_eur']:.2f} ({heat['heat_pct']:.1f}%)\n"
            f"Kill-Switch: {ks_state} | DD-Halt: {dd_state} | Calls heute: {calls_today}\n"
        )
        if stats and stats.get("calibration"):
            cal = stats["calibration"]
            msg += (
                f"\n*Calibration*: Brier {cal['avg_brier']:.3f}, "
                f"p_pred {cal['avg_p_predicted']:.2f} vs actual {cal['actual_win_rate']:.2f}"
            )
            if cal.get("haircut"):
                msg += f" | Haircut aktiv: {cal['haircut']:+.2f}"
        if mistake_line:
            msg += f"\n*Mistakes (last 20 L)*: {mistake_line}"
        if earn_line:
            msg += f"\n\n*Earnings nächste 7d:*\n{earn_line}"
        if macro_line:
            msg += f"\n\n*Macro heute (Vorschau):*\n{macro_line}"

        send_daily_summary(msg)
        mark_weekend_summary_done()
        logger.info("✅ Weekend summary sent")
    except Exception:
        logger.exception("Weekend summary failed")
