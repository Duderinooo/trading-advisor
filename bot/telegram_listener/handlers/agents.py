"""Telegram handlers for manual monitoring-agent triggers.

Pattern: each agent gets one /<name> command that bypasses the schedule + flag
check and runs the agent right now. Useful for verifying a new agent before
flipping its scheduler flag.
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_listener._common import _authorized, telegram_handler

logger = logging.getLogger(__name__)


def _run_agent_sync(name: str):
    """Sync run + return (ok, alert) tuple. Heavy import done lazily so
    handlers module stays cheap at startup."""
    from agents._lib.dispatcher import run_agent
    return run_agent(name, force=True)


async def _trigger_agent(update: Update, name: str, label: str):
    if not _authorized(update):
        return
    await update.message.reply_text(f"⏳ {label} läuft (10-90s)...")
    loop = asyncio.get_event_loop()
    try:
        ok, alert = await loop.run_in_executor(None, _run_agent_sync, name)
    except Exception as e:
        logger.exception("agent trigger failed: %s", name)
        await update.message.reply_text(f"❌ {label} crashed: {str(e)[:200]}")
        return
    if not ok:
        await update.message.reply_text(f"⚠️ {label}: {alert or 'failed'}")
        return
    if alert:
        await update.message.reply_text(alert)
    else:
        await update.message.reply_text(f"✅ {label} ok — nothing to report")


@telegram_handler
async def bug_watcher_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _trigger_agent(update, "bug-watcher", "🐛 bug-watcher")


@telegram_handler
async def health_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _trigger_agent(update, "health-inspector", "❤️‍🩹 health-inspector")


@telegram_handler
async def postmortem_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _trigger_agent(update, "eod-postmortem", "📒 eod-postmortem")


@telegram_handler
async def calibrate_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _trigger_agent(update, "weekly-calibrator", "🎚 weekly-calibrator")


@telegram_handler
async def backlog_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _trigger_agent(update, "backlog-keeper", "📋 backlog-keeper")
