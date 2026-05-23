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
async def watch_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manually add a watch-level. Format: /watch TICKER TYPE @PREIS [thesis...]

    Example: /watch RWE.DE breakout_long @62.50 Vol-Spike + Analyst-Upgrade

    Types: breakout_long | support_bounce | resistance_reject | inverse_etf_entry
    Sets valid_until = today + 5 days. Adds to existing list (merge by-ticker, so
    re-running for same ticker REPLACES the old level for that ticker).
    """
    if not _authorized(update):
        return
    args = ctx.args or []
    ticker = None
    wtype = None
    price = None
    thesis_tokens: list[str] = []
    for tok in args:
        upper = tok.upper()
        if tok.startswith("@"):
            try:
                price = float(tok[1:].replace(",", "."))
            except ValueError:
                pass
            continue
        if tok.lower() in _WATCH_TYPES:
            wtype = tok.lower()
            continue
        if ticker is None and _TICKER_RE.match(upper) and any(c.isalpha() for c in upper):
            ticker = upper
            continue
        thesis_tokens.append(tok)
    if not ticker or not wtype or not price:
        await update.message.reply_text(
            "Format: `/watch TICKER TYPE @PREIS [thesis...]`\n"
            f"TYPE: {' | '.join(sorted(_WATCH_TYPES))}\n"
            "Beispiel: `/watch RWE.DE breakout_long @62.50 Vol-Spike + Analyst Upgrade`",
            parse_mode="Markdown",
        )
        return
    thesis = " ".join(thesis_tokens).strip()[:120] or f"Manual {wtype} watch"

    valid_until = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")

    new_level = {
        "ticker": ticker,
        "type": wtype,
        "trigger_price": price,
        "thesis": thesis,
        "valid_until": valid_until,
        "source": "manual_/watch",
        "created_date": datetime.now().strftime("%Y-%m-%d"),
    }

    with portfolio_lock:
        portfolio = load_portfolio()
        existing = portfolio.get("watch_levels", [])
        kept = [w for w in existing if (w.get("ticker") or "").upper() != ticker]
        portfolio["watch_levels"] = kept + [new_level]
        save_portfolio(portfolio)
        new_count = len(portfolio["watch_levels"])

    await update.message.reply_text(
        f"👀 *Watchlevel gesetzt*\n"
        f"`{ticker}` {wtype} @ €{price:.2f}\n"
        f"These: _{thesis}_\n"
        f"Valid bis: {valid_until}\n"
        f"Σ Watchlevels: {new_count}",
        parse_mode="Markdown",
    )


@telegram_handler
async def watchremove_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Remove watch-level(s) for a ticker. Format: /watchremove TICKER"""
    if not _authorized(update):
        return
    if not ctx.args:
        await update.message.reply_text(
            "Format: `/watchremove TICKER`",
            parse_mode="Markdown",
        )
        return
    ticker = ctx.args[0].upper()
    with portfolio_lock:
        portfolio = load_portfolio()
        existing = portfolio.get("watch_levels", [])
        kept = [w for w in existing if (w.get("ticker") or "").upper() != ticker]
        removed = len(existing) - len(kept)
        if removed == 0:
            await update.message.reply_text(
                f"❓ Keine Watchlevels für {ticker} gefunden.",
            )
            return
        portfolio["watch_levels"] = kept
        save_portfolio(portfolio)
    await update.message.reply_text(
        f"❌ {removed} Watchlevel(s) für {ticker} entfernt. Σ verbleibend: {len(kept)}.",
    )


@telegram_handler
async def watchclear_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Hard-wipe ALL watch-levels. Single-step (User ist full-trust Operator)."""
    if not _authorized(update):
        return
    with portfolio_lock:
        portfolio = load_portfolio()
        levels = portfolio.get("watch_levels", [])
        n = len(levels)
        tickers = ", ".join(sorted({(w.get("ticker") or "?") for w in levels}))
        portfolio["watch_levels"] = []
        save_portfolio(portfolio)
    if n == 0:
        await update.message.reply_text("Keine Watchlevels vorhanden — nichts zu löschen.")
        return
    await update.message.reply_text(
        f"🗑️ {n} Watchlevels entfernt.\n_{tickers}_",
        parse_mode="Markdown",
    )


@telegram_handler
async def watchlist_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show all active watch-levels."""
    if not _authorized(update):
        return
    with portfolio_lock:
        portfolio = load_portfolio()
    levels = portfolio.get("watch_levels", [])
    if not levels:
        await update.message.reply_text("Keine aktiven Watchlevels.")
        return
    lines = ["*Aktive Watchlevels:*"]
    for w in levels:
        line = (
            f"• `{w.get('ticker')}` {w.get('type')} @ €{w.get('trigger_price')} "
            f"(bis {w.get('valid_until','?')})"
        )
        if w.get("thesis"):
            line += f"\n  _{w['thesis'][:80]}_"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
