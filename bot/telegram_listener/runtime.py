"""Telegram bootstrap: error handler + Application init + start_listener_thread.

Runs in a background daemon thread with its own asyncio loop. Started from
main.py via start_listener_thread().
"""

import asyncio
import logging
import threading

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from telegram_listener._common import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from telegram_listener.commands import (
    add_handler, audit_handler, cancel_handler, close_handler, confirm_handler,
    dividend_handler, help_handler, killstatus_handler, morning_handler,
    panic_handler, positions_handler, rec_callback_handler, resume_handler,
    stats_handler, watch_handler, watchclear_handler, watchlist_handler,
    watchremove_handler,
)

logger = logging.getLogger(__name__)


async def _global_error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    """Last-resort safety net for anything that escapes per-handler @telegram_handler.
    Logs the error + tries to ping the user (best-effort)."""
    err = ctx.error
    logger.exception("Telegram global error handler caught: %r", err)
    try:
        chat_id = TELEGRAM_CHAT_ID
        if chat_id and ctx.bot is not None:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ *Telegram-Listener-Crash*\n"
                    f"`{type(err).__name__}: {err}`\n"
                    f"_Bot weiter aktiv. Log prüfen._"
                ),
                parse_mode="Markdown",
            )
    except Exception:
        pass


async def _async_run():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_error_handler(_global_error_handler)
    app.add_handler(CommandHandler("confirm", confirm_handler))
    app.add_handler(CommandHandler("add", add_handler))
    app.add_handler(CommandHandler("watch", watch_handler))
    app.add_handler(CommandHandler("watchlist", watchlist_handler))
    app.add_handler(CommandHandler("watchremove", watchremove_handler))
    app.add_handler(CommandHandler("watchclear", watchclear_handler))
    app.add_handler(CommandHandler("close", close_handler))
    app.add_handler(CommandHandler("dividend", dividend_handler))
    app.add_handler(CommandHandler("positions", positions_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CommandHandler("panic", panic_handler))
    app.add_handler(CommandHandler("resume", resume_handler))
    app.add_handler(CommandHandler("killstatus", killstatus_handler))
    app.add_handler(CommandHandler("morning", morning_handler))
    app.add_handler(CommandHandler("audit", audit_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("start", help_handler))
    # Inline-keyboard buttons on entry-rec messages → rec_callback_handler.
    app.add_handler(CallbackQueryHandler(rec_callback_handler, pattern=r"^rec:"))

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

