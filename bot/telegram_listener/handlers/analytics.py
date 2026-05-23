"""All Telegram command handlers — watch/confirm/close/add/portfolio/system/analytics.

Pragmatic single-file home for the 17 handlers + their helpers. Split further
into handlers/ subpackage when individual files outgrow ~250 LOC.

Imports utilities from telegram_listener._common (auth, decorator, parsers,
constants) so this module is import-cheap to load at startup.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

import config
from core import (
    portfolio_lock, load_portfolio, save_portfolio, add_cash_movement,
    build_trade_dict,
    get_market_data,
    risk_halt_status, set_kill_switch, kill_switch_active,
    maintain_drawdown_state, compute_slippage_budget, get_period_return,
)
from memory import log_trade, MEMPALACE_AVAILABLE

from telegram_listener._common import (
    _MISTAKE_CLASS_MAP, _NUMBER_RE, _TICKER_RE, _WATCH_TYPES,
    _authorized, _parse_close_args, _parse_confirm_args,
    _parse_dividend_args, telegram_handler,
)

logger = logging.getLogger(__name__)

@telegram_handler
async def stats_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """`/stats` — surface compute_hit_stats + gate FNR + calibration trend.

    No args needed. Shows: total trades, win rate, avg R, per-setup expectancy,
    Brier calibration, top blocking gates with false-negative rate."""
    if not _authorized(update):
        return
    with portfolio_lock:
        portfolio = load_portfolio()
    from core.portfolio import compute_hit_stats
    stats = compute_hit_stats(
        portfolio.get("closed_trades", []), portfolio.get("cash_movements", []),
    )
    if not stats:
        await update.message.reply_text(
            "📊 *Stats* — kein closed-trades-Sample bisher.",
            parse_mode="Markdown",
        )
        return

    lines = ["📊 *Stats*\n"]
    lines.append(
        f"_Total:_ {stats.get('total', 0)} trades | "
        f"Win-rate: {stats.get('win_rate', 0) * 100:.1f}% | "
        f"Avg R: {stats.get('avg_r', 0):+.2f}"
    )

    by_setup = stats.get("by_setup_type") or {}
    if by_setup:
        # Sort by total desc, take top 6.
        top = sorted(
            by_setup.items(), key=lambda kv: -(kv[1].get("total") or 0),
        )[:6]
        lines.append("\n*Per setup_type:*")
        for setup, d in top:
            n = d.get("total", 0)
            wr = d.get("rate", 0)
            ap = d.get("avg_pnl_pct", 0)
            lines.append(
                f"  • `{setup}` — n={n} wr={wr:.0f}% avg P&L={ap:+.1f}%"
            )

    cal = stats.get("calibration") or {}
    if cal:
        hc = cal.get("haircut") or 0
        lines.append(
            f"\n*Calibration:* Brier {cal.get('avg_brier', 0):.3f} | "
            f"p̂={cal.get('avg_p_predicted', 0):.2f} vs actual="
            f"{cal.get('actual_win_rate', 0):.2f} | "
            f"haircut={hc:+.2f}"
        )

    # Gate false-negative rates (read from outcomes if any)
    try:
        from core.llm.telemetry.outcomes import gate_false_negative_rates
        fnr = gate_false_negative_rates()
        if fnr:
            lines.append("\n*Gate FNR (blocked-but-would-have-won):*")
            top_gates = sorted(
                fnr.items(),
                key=lambda kv: -kv[1].get("total", 0),
            )[:5]
            for gate, d in top_gates:
                lines.append(
                    f"  • `{gate}` — n={d['total']} would_win={d['would_win']} "
                    f"FNR={d['false_negative_rate']:.2%}"
                )
    except Exception as e:
        logger.warning("FNR aggregation failed in /stats: %s", e)

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@telegram_handler
async def audit_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """`/audit TICKER` — show last N DecisionResult trees for that ticker.
    Reads analytics/decisions.jsonl (written by recs/entry.py _persist_decision)."""
    if not _authorized(update):
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/audit TICKER` — letzte 5 Gate-Pipeline-Decisions",
            parse_mode="Markdown",
        )
        return
    ticker = args[0].upper()
    limit = 5
    if len(args) >= 2:
        try:
            limit = max(1, min(20, int(args[1])))
        except ValueError:
            pass

    import json as _json
    import os as _os
    from pathlib import Path as _Path
    decisions_path = (
        _Path(_os.path.dirname(_os.path.abspath(__file__))) / "analytics" / "decisions.jsonl"
    )
    if not decisions_path.exists():
        await update.message.reply_text(
            f"Keine Decisions-History gefunden (`{decisions_path.name}` fehlt).",
            parse_mode="Markdown",
        )
        return

    matches: list[dict] = []
    try:
        with decisions_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tree = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if (tree.get("ticker") or "").upper() == ticker:
                    matches.append(tree)
    except Exception as e:
        await update.message.reply_text(f"Read-Fehler: `{e}`", parse_mode="Markdown")
        return

    if not matches:
        await update.message.reply_text(
            f"Keine Decisions für `{ticker}` gefunden.", parse_mode="Markdown",
        )
        return

    # Show last `limit` matches, newest first
    matches = matches[-limit:][::-1]
    blocks: list[str] = [f"🔍 *Audit `{ticker}`* — letzte {len(matches)} Decision(s):\n"]
    for tree in matches:
        ts = tree.get("timestamp", "?")
        decision = tree.get("decision", "?")
        emoji = "✅" if decision == "PASS" else "🛑"
        head = f"\n{emoji} *{ts}* — {decision}"
        if tree.get("blocked_by"):
            head += f" by `{tree['blocked_by']}`"
        blocks.append(head)
        for step in tree.get("path", []):
            blocks.append(f"  • {step}")

    msg = "\n".join(blocks)
    # Telegram message-length cap 4096; truncate gracefully.
    if len(msg) > 3800:
        msg = msg[:3800] + "\n…(truncated)"
    await update.message.reply_text(msg, parse_mode="Markdown")
