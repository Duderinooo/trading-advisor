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
    auto_mistake_class_from_alpha,
)
from memory import log_trade, MEMPALACE_AVAILABLE

from telegram_listener._common import (
    _MISTAKE_CLASS_MAP, _NUMBER_RE, _TICKER_RE, _WATCH_TYPES,
    _authorized, _parse_close_args, _parse_confirm_args,
    _parse_dividend_args, telegram_handler,
)

logger = logging.getLogger(__name__)

@telegram_handler
async def add_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manual add to existing position. Format: /add TICKER STK X @PREIS

    Example: /add RWE.DE STK 2 @61.60
    Pyramides into existing open_trade with weighted-avg entry. Original SL/TP
    stay. No pending-rec needed (skips Claude validation — user is full-trust).
    Risk-halt + cash check still apply.
    """
    if not _authorized(update):
        return
    args = ctx.args or []
    ticker = None
    shares = None
    price = None
    for tok in args:
        upper = tok.upper()
        if upper == "STK":
            continue
        if tok.startswith("@"):
            try:
                price = float(tok[1:].replace(",", "."))
            except ValueError:
                pass
            continue
        normalized = tok.replace(",", ".")
        if _NUMBER_RE.match(normalized):
            shares = float(normalized)
            continue
        if _TICKER_RE.match(upper) and any(c.isalpha() for c in upper):
            ticker = upper
    if not ticker or not shares or not price or shares <= 0 or price <= 0:
        await update.message.reply_text(
            "Format: `/add TICKER STK X @PREIS`\n"
            "Beispiel: `/add RWE.DE STK 2 @61.60`",
            parse_mode="Markdown",
        )
        return

    actual_added = round(shares * price, 2)

    with portfolio_lock:
        portfolio = load_portfolio()
        open_trade = next(
            (t for t in portfolio.get("open_trades", [])
             if (t.get("ticker") or "").upper() == ticker),
            None,
        )
        if open_trade is None:
            await update.message.reply_text(
                f"❌ Keine offene Position für {ticker}. /add nur für Pyramiding.",
            )
            return

        halt = risk_halt_status(portfolio)
        if halt["halt"]:
            await update.message.reply_text(
                "⛔ *Risk-Halt aktiv — /add blockiert*\n"
                + "\n".join(f"• {r}" for r in halt["reasons"]),
                parse_mode="Markdown",
            )
            return

        cash = float(portfolio.get("cash_eur", 0) or 0)
        if actual_added > cash + 0.01:
            await update.message.reply_text(
                f"⚠️ Nicht genug Cash: brauche €{actual_added:.2f}, habe €{cash:.2f}.",
            )
            return

        old_shares = float(open_trade.get("shares", 0) or 0)
        old_size = float(open_trade.get("size_eur", 0) or 0)
        new_shares = round(old_shares + shares, 4)
        new_size = round(old_size + actual_added, 2)
        new_entry = round(new_size / new_shares, 4) if new_shares > 0 else price

        open_trade["shares"] = new_shares
        open_trade["size_eur"] = new_size
        open_trade["entry_price"] = new_entry
        open_trade.setdefault("add_history", []).append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "added_shares": shares,
            "added_size_eur": actual_added,
            "fill_price": price,
            "source": "manual_/add",
        })
        portfolio["cash_eur"] = round(cash - actual_added, 2)
        save_portfolio(portfolio)

    if MEMPALACE_AVAILABLE:
        try:
            log_trade(open_trade, "ADDED_MANUAL", f"manual /add +{shares} @ €{price}")
        except Exception:
            logger.exception("MemPalace log_trade /add failed")

    sl = open_trade.get("stop_loss")
    sl_str = f"€{sl:.2f}" if sl else "–"
    tp = open_trade.get("take_profit")
    tp_str = (
        " / ".join(f"€{t:.2f}" for t in tp) if isinstance(tp, list)
        else (f"€{tp:.2f}" if tp else "–")
    )
    await update.message.reply_text(
        f"✅ *{ticker} aufgestockt (manual /add)*\n"
        f"+{shares:g} × €{price:.2f} = €{actual_added:.2f}\n"
        f"Neu: {new_shares:g} Stk | Avg-Entry €{new_entry:.4f} | Σ €{new_size:.2f}\n"
        f"SL: {sl_str} | TP: {tp_str}\n"
        f"Cash: €{portfolio['cash_eur']:.2f}",
        parse_mode="Markdown",
    )


@telegram_handler
async def close_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    ticker, exit_price, mistake_tag = _parse_close_args(ctx.args or [])
    if not ticker:
        tags_help = ", ".join(sorted(config.MISTAKE_TAGS))
        await update.message.reply_text(
            f"Format: `/close TICKER [@exit_preis] [#tag]`\nTags: {tags_help}",
            parse_mode="Markdown",
        )
        return

    if exit_price is None:
        try:
            data = get_market_data([ticker])
            exit_price = data.get(ticker, {}).get("price")
        except Exception:
            exit_price = None
        if not exit_price:
            await update.message.reply_text(
                f"❌ Kein Preis für {ticker}. Manuell angeben: `/close {ticker} @PREIS`",
                parse_mode="Markdown",
            )
            return
    exit_price = float(exit_price)

    with portfolio_lock:
        portfolio = load_portfolio()
        open_trades = portfolio.get("open_trades", [])

        idx = None
        for i, t in enumerate(open_trades):
            if t.get("ticker", "").upper() == ticker:
                idx = i
                break

        if idx is None:
            await update.message.reply_text(f"❌ Keine offene Position für {ticker}.")
            return

        trade = open_trades.pop(idx)
        entry = float(trade.get("entry_price", 0) or 0)
        shares = float(trade.get("shares", 0) or 0)  # TR supports Bruchstücke (0.55)
        pnl_eur = (exit_price - entry) * shares if entry and shares else 0.0
        pnl_pct = ((exit_price - entry) / entry * 100) if entry else 0.0

        closed = {
            **trade,
            "exit_price": exit_price,
            "exit_reason": "MANUAL",
            "exit_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "pnl_eur": round(pnl_eur, 2),
            "pnl_pct": round(pnl_pct, 2),
            "status": "closed",
        }
        # Alpha vs Beta attribution: trade-return − SPY-return over same period.
        # >0 = real skill (beat market); <0 = lost vs market. Drives loss interpretation:
        # negative pnl with positive alpha = market noise, not setup failure.
        try:
            entry_date = trade.get("entry_date", "")
            spy_ret = get_period_return("SPY5.DE", entry_date, closed["exit_date"])
            if spy_ret is not None:
                closed["spy_return_pct"] = spy_ret
                closed["alpha_pct"] = round(pnl_pct - spy_ret, 2)
        except Exception:
            logger.exception("SPY-attribution fetch failed for %s", ticker)
        if pnl_pct <= 0 and mistake_tag:
            closed["mistake_tag"] = mistake_tag
            closed["mistake_class"] = _MISTAKE_CLASS_MAP.get(mistake_tag, "other")
        elif pnl_pct <= 0:
            # No user #tag → auto-derive class from alpha so the learning loop
            # still gets a class (2026-06-10: all losses were untagged).
            auto = auto_mistake_class_from_alpha(closed.get("alpha_pct"))
            if auto:
                closed["mistake_tag"], closed["mistake_class"] = auto
                closed["mistake_class_auto"] = True
            else:
                closed["mistake_tag"] = None
                closed["mistake_class"] = "untagged"
        p_win = trade.get("p_win")
        if isinstance(p_win, (int, float)) and 0.0 <= p_win <= 1.0:
            outcome = 1 if pnl_pct > 0 else 0
            closed["brier"] = round((p_win - outcome) ** 2, 4)
            closed["outcome"] = outcome
        portfolio.setdefault("closed_trades", []).append(closed)
        portfolio["cash_eur"] = round(float(portfolio.get("cash_eur", 0) or 0) + (exit_price * shares), 2)
        portfolio["open_trades"] = open_trades

        maintain_drawdown_state(portfolio)
        # Refresh correlation matrix: when this close drops below 2 open positions,
        # snapshot becomes meaningless. Above 2, matrix needs to drop the closed
        # ticker. Bug 2026-05-07: SIE.DE + RWE.DE matrix persisted in dashboard
        # for 2 days after both were closed because /close path didn't refresh.
        try:
            from core.metrics import compute_correlation_snapshot
            from core.portfolio.runtime_store import load_runtime, save_runtime
            snap = compute_correlation_snapshot(portfolio)
            rt = load_runtime()
            if snap is None:
                rt.pop("correlation_matrix", None)
            else:
                rt["correlation_matrix"] = snap
            save_runtime(rt)
        except Exception:
            logger.exception("Correlation snapshot refresh failed at /close")
        save_portfolio(portfolio)
        new_cash = portfolio["cash_eur"]

    if MEMPALACE_AVAILABLE:
        try:
            ctx_line = f"Exit @ €{exit_price:.2f}"
            if closed.get("mistake_tag"):
                ctx_line += f" | mistake={closed['mistake_tag']}/{closed['mistake_class']}"
            log_trade(closed, "CLOSED_MANUAL", ctx_line)
        except Exception:
            logger.exception("MemPalace log_trade failed")

    emoji = "🎉" if pnl_eur > 0 else "📉"
    tag_line = ""
    if closed.get("mistake_tag"):
        tag_line = f"\nMistake: #{closed['mistake_tag']} ({closed['mistake_class']})"
    elif pnl_pct <= 0:
        tag_line = f"\n_Kein #tag → class=untagged. Nächstes Mal: `/close {ticker} @X #grund`_"
    await update.message.reply_text(
        f"{emoji} *{ticker} geschlossen*\n"
        f"Entry €{entry:.2f} → Exit €{exit_price:.2f}\n"
        f"P&L: €{pnl_eur:+.2f} ({pnl_pct:+.2f}%){tag_line}\n"
        f"Cash: €{new_cash:.2f}",
        parse_mode="Markdown",
    )


@telegram_handler
async def cancel_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    reply_msg_id = None
    if update.message and update.message.reply_to_message:
        reply_msg_id = update.message.reply_to_message.message_id

    ticker = (ctx.args[0].upper() if ctx.args else None)

    with portfolio_lock:
        portfolio = load_portfolio()
        pending = portfolio.get("pending_recommendations", [])

        removed = None
        if reply_msg_id is not None:
            for i, r in enumerate(pending):
                if r.get("message_id") == reply_msg_id:
                    removed = pending.pop(i)
                    break
        elif ticker:
            for i in range(len(pending) - 1, -1, -1):
                if pending[i].get("ticker", "").upper() == ticker:
                    removed = pending.pop(i)
                    break

        if removed is None:
            await update.message.reply_text("❓ Keine passende pending-Empfehlung gefunden.")
            return

        portfolio["pending_recommendations"] = pending
        save_portfolio(portfolio)

    await update.message.reply_text(f"❌ Empfehlung {removed.get('ticker')} verworfen.")
