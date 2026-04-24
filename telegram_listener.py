"""Telegram command listener: manual trade confirmation + close + portfolio queries.

Runs in a background thread with its own asyncio loop, so the main event-check
loop is never blocked. All portfolio mutations acquire `portfolio_lock`.
"""

import os
import re
import logging
import threading
import asyncio
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import config
from core import (
    portfolio_lock, load_portfolio, save_portfolio, get_market_data,
    risk_halt_status, set_kill_switch, kill_switch_active,
)
from memory import log_trade, MEMPALACE_AVAILABLE

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


_TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}(?:\.[A-Z]{1,3})?$")


def _authorized(update: Update) -> bool:
    """Only accept commands from the configured chat."""
    if not update.effective_chat or not TELEGRAM_CHAT_ID:
        return False
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)


_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


def _parse_confirm_args(args: list[str]) -> tuple[str | None, float | None, float | None]:
    """Parse `/confirm` arguments. All three components are optional.

    Shares are float so TR Bruchstücke (e.g. 0.55) work.

    Examples:
        []                   -> (None, None, None)          # use rec defaults
        ["3"]                -> (None, None, 3.0)
        ["0.55"]             -> (None, None, 0.55)
        ["@172.50", "3"]     -> (None, 172.50, 3.0)
        ["NVD.DE", "3"]      -> ("NVD.DE", None, 3.0)
    """
    ticker = None
    price = None
    shares = None
    for tok in args:
        if tok.startswith("@"):
            try:
                price = float(tok[1:])
            except ValueError:
                pass
            continue
        if _NUMBER_RE.match(tok):
            shares = float(tok)
            continue
        upper = tok.upper()
        if _TICKER_RE.match(upper) and any(c.isalpha() for c in upper):
            ticker = upper
    return ticker, price, shares


_MISTAKE_CLASS_MAP = {
    "thesis_wrong": "prediction",
    "timing_early": "timing",
    "timing_late": "timing",
    "whipsaw": "timing",
    "slippage": "execution",
    "sl_too_tight": "execution",
    "news_shock": "external",
    "regime_shift": "external",
}


def _parse_close_args(args: list[str]) -> tuple[str | None, float | None, str | None]:
    ticker = None
    exit_price = None
    tag = None
    for tok in args:
        if tok.startswith("@"):
            try:
                exit_price = float(tok[1:])
            except ValueError:
                pass
            continue
        if tok.startswith("#"):
            t = tok[1:].lower()
            if t in config.MISTAKE_TAGS:
                tag = t
            continue
        upper = tok.upper()
        if _TICKER_RE.match(upper) and any(c.isalpha() for c in upper):
            ticker = upper
    return ticker, exit_price, tag


async def confirm_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    reply_msg_id = None
    if update.message and update.message.reply_to_message:
        reply_msg_id = update.message.reply_to_message.message_id

    ticker_arg, price_override, shares_override = _parse_confirm_args(ctx.args or [])

    with portfolio_lock:
        portfolio = load_portfolio()
        pending = portfolio.get("pending_recommendations", [])

        rec = None
        rec_idx = None

        # Prefer exact match by replied-to message_id
        if reply_msg_id is not None:
            for i, r in enumerate(pending):
                if r.get("message_id") == reply_msg_id:
                    rec, rec_idx = r, i
                    break

        # Fall back to most-recent pending for the given ticker
        if rec is None and ticker_arg:
            for i in range(len(pending) - 1, -1, -1):
                if pending[i].get("ticker", "").upper() == ticker_arg:
                    rec, rec_idx = pending[i], i
                    break

        if rec is None:
            await update.message.reply_text(
                "❓ Keine passende Empfehlung.\n"
                "Reply auf die Empfehlung oder `/confirm TICKER [shares] [@preis]`.",
                parse_mode="Markdown",
            )
            return

        rec_entry = float(rec.get("entry_price", 0) or 0)
        entry = price_override if price_override is not None else rec_entry
        if entry <= 0:
            await update.message.reply_text("❌ Kein gültiger Entry-Preis. `@PREIS` angeben.")
            return

        # Slippage gate: if user passed a filled price, reject when too far from rec.
        slippage_pct = 0.0
        if price_override is not None and rec_entry > 0:
            slippage_pct = (entry - rec_entry) / rec_entry * 100
            if abs(slippage_pct) > config.MAX_ENTRY_SLIPPAGE_PERCENT:
                await update.message.reply_text(
                    f"⛔ *Slippage-Abbruch*\n"
                    f"Rec €{rec_entry:.2f} vs. Fill €{entry:.2f} "
                    f"({slippage_pct:+.2f}%) > {config.MAX_ENTRY_SLIPPAGE_PERCENT}%.\n"
                    f"Neu quoten oder `/cancel`.",
                    parse_mode="Markdown",
                )
                return

        # Risk-halt re-check at confirm time (state may have moved since rec was posted)
        halt = risk_halt_status(portfolio)
        if halt["halt"]:
            await update.message.reply_text(
                "⛔ *Risk-Halt aktiv — Confirm blockiert*\n"
                + "\n".join(f"• {r}" for r in halt["reasons"]),
                parse_mode="Markdown",
            )
            return

        # SL mandatory for full-trust execution
        sl = rec.get("stop_loss")
        if not sl or float(sl) <= 0 or float(sl) >= entry:
            await update.message.reply_text(
                "❌ Kein gültiger Stop-Loss auf Empfehlung. Confirm verweigert (Ruin-Schutz)."
            )
            return

        size_eur = float(rec.get("size_eur", 0) or 0)
        if shares_override is not None:
            shares = float(shares_override)
        else:
            # TR supports Bruchstücke — keep 4 decimals of precision
            shares = round(size_eur / entry, 4) if entry > 0 else 0.0
            if shares <= 0:
                shares = 1.0

        actual_size = round(shares * entry, 2)
        cash = float(portfolio.get("cash_eur", 0) or 0)
        if actual_size > cash + 0.01:
            max_shares = round(cash / entry, 4) if entry > 0 else 0
            await update.message.reply_text(
                f"⚠️ Nicht genug Cash: brauche €{actual_size:.2f}, habe €{cash:.2f}.\n"
                f"Max möglich: {max_shares} Stück."
            )
            return

        trade = {
            "ticker": rec["ticker"],
            "entry_price": entry,
            "shares": shares,
            "size_eur": actual_size,
            "stop_loss": rec.get("stop_loss"),
            "take_profit": rec.get("take_profit"),
            "trailing_stop_pct": rec.get("trailing_stop_pct"),
            "conviction": rec.get("conviction"),
            "p_win": rec.get("p_win"),
            "thesis": rec.get("thesis"),
            "entry_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status": "open",
            "rec_entry_price": rec_entry,
            "slippage_pct": round(slippage_pct, 3),
        }

        portfolio.setdefault("open_trades", []).append(trade)
        portfolio["cash_eur"] = round(cash - actual_size, 2)
        pending.pop(rec_idx)
        portfolio["pending_recommendations"] = pending

        save_portfolio(portfolio)
        new_cash = portfolio["cash_eur"]

    if MEMPALACE_AVAILABLE:
        try:
            log_trade(trade, "CONFIRMED", rec.get("thesis", ""))
        except Exception:
            logger.exception("MemPalace log_trade failed")

    tp = trade["take_profit"]
    tp_str = " / ".join(f"€{t:.2f}" for t in tp) if isinstance(tp, list) else f"€{(tp or 0):.2f}"
    sl_str = f"€{trade['stop_loss']:.2f}" if trade.get("stop_loss") else "–"
    await update.message.reply_text(
        f"✅ *{trade['ticker']} im Portfolio*\n"
        f"{shares:g} × €{entry:.2f} = €{actual_size:.2f}\n"
        f"SL: {sl_str} | TP: {tp_str}\n"
        f"Cash: €{new_cash:.2f}",
        parse_mode="Markdown",
    )


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
        if pnl_pct <= 0 and mistake_tag:
            closed["mistake_tag"] = mistake_tag
            closed["mistake_class"] = _MISTAKE_CLASS_MAP.get(mistake_tag, "other")
        elif pnl_pct <= 0:
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
        lines.append("\n*Offene Positionen:*")
        for t in open_trades:
            ticker = t.get("ticker", "?")
            shares = t.get("shares", 0)
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss")
            tp = t.get("take_profit")
            tp_str = "/".join(f"€{x:.2f}" for x in tp) if isinstance(tp, list) else (f"€{tp:.2f}" if tp else "–")
            sl_str = f"€{sl:.2f}" if sl else "–"
            lines.append(f"• {ticker}: {shares:g}×€{entry:.2f} | SL {sl_str} | TP {tp_str}")
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


async def resume_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    set_kill_switch(False)
    await update.message.reply_text("✅ Kill-Switch AUS. Trading wieder aktiv.")


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


async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "*Befehle:*\n"
        "`/confirm` (reply) — Empfehlung übernehmen, shares auto-berechnet\n"
        "`/confirm 3` (reply) — 3 Stück, rec-Preis\n"
        "`/confirm 3 @172.50` (reply) — 3 Stück, Preis €172.50\n"
        "`/confirm NVD.DE 3 @172.50` — standalone\n"
        "`/close TICKER [@preis] [#tag]` — Position schließen (bei Verlust: #tag = Grund)\n"
        "`/cancel` (reply) — Pending-Empfehlung verwerfen\n"
        "`/positions` — Portfolio anzeigen\n"
        "`/panic [grund]` — Kill-Switch AN (blockt neue Entries + Event-Analysen)\n"
        "`/resume` — Kill-Switch AUS\n"
        "`/killstatus` — Kill-Switch Status",
        parse_mode="Markdown",
    )


async def _async_run():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("confirm", confirm_handler))
    app.add_handler(CommandHandler("close", close_handler))
    app.add_handler(CommandHandler("positions", positions_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CommandHandler("panic", panic_handler))
    app.add_handler(CommandHandler("resume", resume_handler))
    app.add_handler(CommandHandler("killstatus", killstatus_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("start", help_handler))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("🎧 Telegram listener online")

    # Block forever; daemon thread exits with main process.
    await asyncio.Event().wait()


def start_listener_thread() -> threading.Thread | None:
    """Launch the Telegram listener on a background daemon thread."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram listener NOT started: TELEGRAM_BOT_TOKEN/CHAT_ID missing")
        return None

    def _target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_async_run())
        except Exception:
            logger.exception("Telegram listener crashed")

    t = threading.Thread(target=_target, daemon=True, name="telegram_listener")
    t.start()
    return t
