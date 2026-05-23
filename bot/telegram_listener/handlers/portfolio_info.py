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
async def dividend_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    ticker, amount, reason = _parse_dividend_args(ctx.args or [])
    if not ticker or amount is None:
        await update.message.reply_text(
            "Format: `/dividend TICKER AMOUNT [grund]`\n"
            "Beispiel: `/dividend RWE.DE 12.50 Q1 2026 Dividende`",
            parse_mode="Markdown",
        )
        return
    if amount <= 0:
        await update.message.reply_text("❌ Betrag muss > 0 sein.")
        return

    with portfolio_lock:
        portfolio = load_portfolio()
        known_tickers = {
            t.get("ticker", "").upper()
            for t in portfolio.get("open_trades", []) + portfolio.get("closed_trades", [])
        }
        if ticker not in known_tickers:
            await update.message.reply_text(
                f"❌ {ticker} unbekannt (nie gehalten). "
                "Tippfehler? Sonst Position erst öffnen oder Closed-Trade beibehalten."
            )
            return
        add_cash_movement(
            portfolio,
            amount=amount,
            kind="dividend",
            ticker=ticker,
            note=reason,
        )
        save_portfolio(portfolio)
        new_cash = portfolio["cash_eur"]

    note_line = f"\n_„{reason}“_" if reason else ""
    await update.message.reply_text(
        f"💰 *Dividende {ticker} +€{amount:.2f}*{note_line}\n"
        f"Cash: €{new_cash:.2f}",
        parse_mode="Markdown",
    )


@telegram_handler
async def positions_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    with portfolio_lock:
        portfolio = load_portfolio()

    open_trades = portfolio.get("open_trades", [])
    pending = portfolio.get("pending_recommendations", [])
    cash = float(portfolio.get("cash_eur", 0) or 0)

    lines = [f"💰 *Cash:* €{cash:.2f}"]

    if open_trades:
        # Batch live prices for unrealized P&L. Cache hits if morning-prep ran recently.
        live = {}
        try:
            live = get_market_data([t.get("ticker") for t in open_trades if t.get("ticker")])
        except Exception:
            logger.exception("/positions price fetch failed")

        total_unreal_eur = 0.0
        lines.append("\n*Offene Positionen:*")
        for t in open_trades:
            ticker = t.get("ticker", "?")
            shares = float(t.get("shares", 0) or 0)
            entry = float(t.get("entry_price", 0) or 0)
            sl = t.get("stop_loss")
            tp = t.get("take_profit")
            tp_str = "/".join(f"€{x:.2f}" for x in tp) if isinstance(tp, list) else (f"€{tp:.2f}" if tp else "–")
            sl_str = f"€{sl:.2f}" if sl else "–"

            price = (live.get(ticker) or {}).get("price") if isinstance(live.get(ticker), dict) else None
            pnl_line = ""
            if isinstance(price, (int, float)) and entry > 0 and shares > 0:
                pnl_eur = (price - entry) * shares
                pnl_pct = (price - entry) / entry * 100
                total_unreal_eur += pnl_eur
                emoji = "🟢" if pnl_eur >= 0 else "🔴"
                pnl_line = f" | {emoji} €{price:.2f} ({pnl_eur:+.2f}€ / {pnl_pct:+.2f}%)"

            lines.append(
                f"• {ticker}: {shares:g}×€{entry:.2f} | SL {sl_str} | TP {tp_str}{pnl_line}"
            )

        if total_unreal_eur:
            lines.append(f"\n_Σ unrealized: €{total_unreal_eur:+.2f}_")
    else:
        lines.append("\n_Keine offenen Positionen._")

    if pending:
        lines.append("\n*Pending Empfehlungen:*")
        for r in pending:
            lines.append(
                f"• {r.get('ticker', '?')} @ €{(r.get('entry_price') or 0):.2f} "
                f"(Conv {r.get('conviction', 0)}/5)"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
