"""Telegram callback-query handler for inline-keyboard buttons on entry recs.

Entry recommendations posted via `core.llm.handlers.recs.entry` carry an
inline keyboard with two buttons: ❌ Reject and 👁️ Watch. This file
processes the button clicks. /confirm still requires the reply-with-price
flow because the slippage gate needs the actual fill — callback buttons
cover the destructive paths (reject, downgrade to watch) only.

Callback-data scheme:
- "rec:reject" — remove the pending rec entirely
- "rec:watch"  — remove from pending + add a 5-day watch_level at the
                 rec's entry_price (effectively downgrading "execute now"
                 to "alert me if it hits this level")

The pending rec is located via the message_id on the chat message the
button is attached to — same anchor the reply-based /cancel handler
uses, so the two code paths converge on the same lookup logic.
"""

from datetime import datetime, timedelta
import logging

from telegram import Update
from telegram.ext import ContextTypes

from core.portfolio import load_portfolio, portfolio_lock, save_portfolio
from telegram_listener._common import _authorized


logger = logging.getLogger(__name__)


_WATCH_TYPE_BY_DIRECTION = {
    "LONG": "breakout_long",
    "SHORT": "resistance_reject",
}


async def rec_callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Dispatch button clicks on entry-rec messages."""
    cb = update.callback_query
    if cb is None:
        return
    # ACK immediately — Telegram greys the spinner once we answer.
    try:
        await cb.answer()
    except Exception:
        logger.exception("callback ack failed")

    if not _authorized(update):
        return

    data = cb.data or ""
    if not data.startswith("rec:"):
        return
    action = data.split(":", 1)[1]
    msg_id = cb.message.message_id if cb.message else None
    if msg_id is None:
        return

    with portfolio_lock:
        portfolio = load_portfolio()
        pending = portfolio.get("pending_recommendations", [])
        rec = None
        idx = None
        for i, r in enumerate(pending):
            if r.get("message_id") == msg_id:
                rec, idx = r, i
                break
        if rec is None:
            try:
                await cb.message.reply_text(
                    "❓ Keine passende pending-Empfehlung mehr (vielleicht schon "
                    "bestätigt / abgelaufen)."
                )
            except Exception:
                logger.exception("callback reply failed")
            return

        ticker = rec.get("ticker", "?")
        if action == "reject":
            pending.pop(idx)
            portfolio["pending_recommendations"] = pending
            save_portfolio(portfolio)
            reply = f"❌ Empfehlung {ticker} verworfen."

        elif action == "watch":
            # Downgrade entry-rec to a 5-day watch level at the same trigger price.
            entry = rec.get("entry_price")
            if not isinstance(entry, (int, float)) or entry <= 0:
                await cb.message.reply_text(
                    f"❓ Watch nicht möglich für {ticker} — kein gültiger entry_price."
                )
                return
            direction = (rec.get("direction") or "LONG").upper()
            wtype = _WATCH_TYPE_BY_DIRECTION.get(direction, "breakout_long")
            valid_until = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
            thesis = (rec.get("thesis") or f"Downgraded from entry rec")[:120]
            new_level = {
                "ticker": ticker,
                "type": wtype,
                "trigger_price": float(entry),
                "thesis": thesis,
                "valid_until": valid_until,
                "source": "rec_downgrade",
                "created_date": datetime.now().strftime("%Y-%m-%d"),
            }
            existing = portfolio.get("watch_levels", [])
            kept = [
                w for w in existing
                if (w.get("ticker") or "").upper() != ticker.upper()
            ]
            portfolio["watch_levels"] = kept + [new_level]
            pending.pop(idx)
            portfolio["pending_recommendations"] = pending
            save_portfolio(portfolio)
            reply = (
                f"👁️ {ticker} als Watch-Level @ €{entry:.2f} hinterlegt "
                f"(5d gültig). Pending-Rec verworfen."
            )

        else:
            reply = f"❓ Unbekannter callback action: {action!r}"

    # Edit original message so the buttons can't be clicked twice and the
    # outcome is recorded on the source message.
    try:
        original = cb.message.text or ""
        await cb.message.edit_text(
            text=f"{original}\n\n_↳ {reply}_",
            parse_mode="Markdown",
        )
    except Exception:
        # If edit fails (e.g. message too old to edit), reply instead.
        try:
            await cb.message.reply_text(reply)
        except Exception:
            logger.exception("callback final reply failed")
