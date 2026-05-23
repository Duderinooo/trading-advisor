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
async def panic_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    reason = " ".join(ctx.args or []) or "manual panic"
    set_kill_switch(True, reason=reason)
    await update.message.reply_text(
        f"🛑 *KILL-SWITCH AN*\nGrund: {reason}\n"
        f"Keine neuen Entries, keine Event/News-Analysen.\n"
        f"SL/TP-Monitor läuft weiter. `/resume` zum Aufheben.",
        parse_mode="Markdown",
    )


@telegram_handler
async def resume_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    set_kill_switch(False)
    # If morning prep got skipped today by the kill-switch gate, watch_levels stays
    # empty until tomorrow 08:00 unless user re-runs it. Hint so day isn't wasted.
    pf = load_portfolio()
    today = datetime.now().strftime("%Y-%m-%d")
    morning_done = pf.get("last_morning_prep_date") == today
    levels = pf.get("watch_levels", []) or []
    msg = "✅ Kill-Switch AUS. Trading wieder aktiv."
    if morning_done and not levels:
        msg += "\n\n💡 Tipp: `/morning` um Watchlevels zu seedlen — sonst bleibt Tag ohne Setups (Auto-Morgen läuft erst morgen 08:00)."
    await update.message.reply_text(msg)


@telegram_handler
async def killstatus_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    with portfolio_lock:
        p = load_portfolio()
    if kill_switch_active(p):
        await update.message.reply_text(
            f"🛑 AKTIV seit {p.get('kill_switch_ts','?')} — {p.get('kill_switch_reason','?')}"
        )
    else:
        await update.message.reply_text("✅ Kill-Switch AUS.")


@telegram_handler
async def morning_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manual re-run of morning prep (re-populates watch_levels)."""
    if not _authorized(update):
        return
    await update.message.reply_text("⏳ Morning Prep läuft (Sonnet-Call, ~10s)...")
    try:
        # Lazy import to avoid circular deps with main at module load.
        from main import run_morning_prep
        # Clear daily-dedup flag so run_morning_prep doesn't short-circuit.
        with portfolio_lock:
            pf = load_portfolio()
            pf.pop("last_morning_prep_date", None)
            save_portfolio(pf)
        # force=True bypasses kill-switch gate — manual /morning is intentional.
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_morning_prep(force=True)
        )
        pf = load_portfolio()
        levels = pf.get("watch_levels", [])
        from core.llm.telemetry.trace_store import load_traces
        tr = load_traces().get("last_morning_trace") or {}
        # Detect skipped analyze_portfolio (daily-cap etc.): trace ts older than 90s.
        tr_fresh = False
        if tr.get("ts"):
            try:
                from datetime import datetime as _dt
                age = (_dt.now() - _dt.strptime(tr["ts"], "%Y-%m-%d %H:%M:%S")).total_seconds()
                tr_fresh = age < 90
            except Exception:
                pass
        lines = [f"✅ Morning Prep fertig. {len(levels)} Watch-Level gesetzt."]
        if levels:
            tickers = ", ".join((lvl.get("ticker") or "?") for lvl in levels[:8])
            lines.append(tickers)
        if not tr_fresh:
            lines.append(
                "⚠️ Kein frischer Trace — analyze_portfolio übersprungen "
                "(daily-cap? check bot.log)."
            )
        elif tr.get("malformed_tool_input") or tr.get("truncated"):
            lines.append("⚠️ Trace zeigt Problem — bot.log + portfolio.json checken.")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.exception("Manual morning prep failed")
        await update.message.reply_text(f"❌ Morning Prep Fehler: {e}")


@telegram_handler
async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "*Befehle:*\n"
        "`/confirm` (reply) — Empfehlung übernehmen, shares auto-berechnet\n"
        "`/confirm 3` (reply) — 3 Stück, rec-Preis\n"
        "`/confirm 3 @172.50` (reply) — 3 Stück, Preis €172.50\n"
        "`/confirm NVD.DE 3 @172.50` — standalone\n"
        "`/add TICKER STK X @PREIS` — manuell aufstocken (z.B. `/add RWE.DE STK 2 @61.60`)\n"
        "`/watch TICKER TYPE @PREIS [thesis]` — Watchlevel manuell setzen\n"
        "`/watchlist` — alle aktiven Watchlevels anzeigen\n"
        "`/watchremove TICKER` — Watchlevel(s) für Ticker entfernen\n"
        "`/watchclear` — ALLE Watchlevels löschen\n"
        "`/close TICKER [@preis] [#tag]` — Position schließen (bei Verlust: #tag = Grund)\n"
        "`/dividend TICKER AMOUNT [grund]` — Dividende verbuchen (Cash + Equity-Curve)\n"
        "`/cancel` (reply) — Pending-Empfehlung verwerfen\n"
        "`/positions` — Portfolio anzeigen\n"
        "`/morning` — Morning Prep manuell neu laufen lassen\n"
        "`/audit TICKER [n]` — letzte N Gate-Pipeline-Decisions (default 5)\n"
        "`/stats` — Win-rate, per-setup expectancy, Brier-calibration, Gate-FNR\n"
        "`/panic [grund]` — Kill-Switch AN (blockt neue Entries + Event-Analysen)\n"
        "`/resume` — Kill-Switch AUS\n"
        "`/killstatus` — Kill-Switch Status",
        parse_mode="Markdown",
    )
