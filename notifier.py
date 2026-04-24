"""Telegram notification handler."""

import os
import re
import asyncio
import logging

from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Telegram max message length
MAX_MESSAGE_LENGTH = 4000  # Leave buffer under 4096


def _chunk_message(message: str) -> list[str]:
    """
    Split long message into chunks at logical boundaries.
    Priority: section headers (##, **), then paragraphs, then hard limit.
    """
    if len(message) <= MAX_MESSAGE_LENGTH:
        return [message]

    chunks = []
    
    # Split by major section markers (## headers or **bold headers**)
    section_pattern = r'(?=\n(?:##|\*\*[A-ZÄÖÜ]|📊|🚨|🟢|🔴|🟡|💡|⚠️))'
    sections = re.split(section_pattern, message)
    
    current_chunk = ""
    
    for section in sections:
        section = section.strip()
        if not section:
            continue
            
        # If section alone is too long, split by paragraphs
        if len(section) > MAX_MESSAGE_LENGTH:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            
            # Split by double newlines (paragraphs)
            paragraphs = section.split("\n\n")
            for para in paragraphs:
                if len(current_chunk) + len(para) + 2 <= MAX_MESSAGE_LENGTH:
                    current_chunk += para + "\n\n"
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    # If paragraph still too long, hard split
                    if len(para) > MAX_MESSAGE_LENGTH:
                        for i in range(0, len(para), MAX_MESSAGE_LENGTH):
                            chunks.append(para[i:i + MAX_MESSAGE_LENGTH])
                        current_chunk = ""
                    else:
                        current_chunk = para + "\n\n"
        
        # Section fits - try to add to current chunk
        elif len(current_chunk) + len(section) + 2 <= MAX_MESSAGE_LENGTH:
            current_chunk += section + "\n\n"
        else:
            # Start new chunk
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = section + "\n\n"
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return chunks if chunks else [message[:MAX_MESSAGE_LENGTH]]


async def _send_message_async(message: str) -> int | None:
    """Send a message via Telegram (async). Auto-chunks long messages.
    Returns the first chunk's message_id (used as anchor for reply-based commands)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured. Message:\n%s", message)
        return None

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    chunks = _chunk_message(message)
    first_id: int | None = None

    for i, chunk in enumerate(chunks):
        sent = None
        try:
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=chunk,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            if i < len(chunks) - 1:
                await asyncio.sleep(0.3)
        except TelegramError as e:
            if "parse" in str(e).lower():
                try:
                    sent = await bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=chunk,
                        disable_web_page_preview=True,
                    )
                except TelegramError as e2:
                    logger.error("Telegram send failed: %s", e2)
            else:
                logger.error("Telegram send failed: %s", e)

        if sent is not None and first_id is None:
            first_id = sent.message_id

    return first_id


def send_notification(message: str) -> int | None:
    """Send a notification via Telegram. Returns first message_id on success, None on failure.

    Safe from both sync contexts (main loop) and async contexts (listener thread):
    if a loop is already running, routes via a helper thread; otherwise uses asyncio.run.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_send_message_async(message))

    # Running inside an event loop — delegate to a short-lived helper thread with its own loop.
    import threading
    result: list[int | None] = [None]

    def _runner():
        result[0] = asyncio.run(_send_message_async(message))

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=30)
    return result[0]


def send_trade_signal(action: str, ticker: str, reason: str, urgency: str = "normal"):
    """Send a formatted trade signal."""
    emoji = {
        "BUY": "🟢",
        "SELL": "🔴", 
        "HOLD": "🟡",
        "WATCH": "👀"
    }.get(action.upper(), "📊")
    
    urgency_marker = "🚨 " if urgency == "high" else ""
    
    message = f"""{urgency_marker}{emoji} *{action.upper()}* Signal

*Ticker:* `{ticker}`
*Reason:* {reason}

_Execute on Trade Republic if you agree with this analysis._"""
    
    send_notification(message)


def send_daily_summary(summary: str):
    """Send portfolio analysis summary."""
    # Just send the summary, no wrapper needed
    send_notification(summary)


def send_alert(title: str, message: str):
    """Send a general alert."""
    send_notification(f"⚠️ *{title}*\n\n{message}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    send_notification("🤖 Trading Advisor bot is now running!")
