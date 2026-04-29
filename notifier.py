"""Telegram notification handler."""

import os
import re
import asyncio
import logging
import threading
import time

from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Alert dedup: drop repeats of same title within window. Defends against
# bugs that spam send_alert in a tight retry loop (incident 2026-04-29:
# cache_control bug + missing mark-done sent 15× same error in minutes).
_ALERT_DEDUP_WINDOW_SEC = 600  # 10 min
_alert_last_seen: dict[str, float] = {}
_alert_lock = threading.Lock()

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
    """Send a general alert. Dedup by title within _ALERT_DEDUP_WINDOW_SEC."""
    now = time.monotonic()
    _stale_cutoff = now - 2 * _ALERT_DEDUP_WINDOW_SEC
    with _alert_lock:
        # Prune entries past 2× window — keeps dict bounded across long runs.
        for k in [k for k, t in _alert_last_seen.items() if t < _stale_cutoff]:
            del _alert_last_seen[k]
        last = _alert_last_seen.get(title)
        if last is not None and (now - last) < _ALERT_DEDUP_WINDOW_SEC:
            logger.warning("send_alert suppressed (dedup): %s", title)
            return
        _alert_last_seen[title] = now
    send_notification(f"⚠️ *{title}*\n\n{message}")


_VALID_ACTIONS = {"ENTRY", "EXIT", "ADD", "REDUCE", "CLOSE"}


def _word_truncate(s: str, limit: int) -> str:
    """Cut at last word boundary ≤ limit. Avoids 'bestätigt Aufwä' mid-word cuts
    that look like display-corruption to the user (Audit 2026-04-28)."""
    if not s or len(s) <= limit:
        return s or ""
    cut = s[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def send_actionable(
    action: str,
    ticker: str,
    size: str | None,
    reason: str,
    *,
    conviction: int | None = None,
    extras: dict | None = None,
) -> int | None:
    """Schema-validated actionable trade signal.

    Schema: 🎯 ACTION | TICKER | SIZE\\nGrund: REASON\\nConv X/5\\nextras...

    Returns first message_id on success, None when validation drops the message
    (logged as warning so the caller can grep what was suppressed).
    """
    a = (action or "").upper().strip()
    if a not in _VALID_ACTIONS:
        logger.warning("send_actionable rejected: action=%r not in %s", action, sorted(_VALID_ACTIONS))
        return None
    t = (ticker or "").upper().strip()
    if not t or not (reason or "").strip():
        logger.warning("send_actionable rejected: ticker=%r reason=%r", ticker, reason)
        return None
    reason_clean = _word_truncate(reason.strip(), 250)
    size_part = f" | {size}" if size else ""
    conv_part = (
        f"\nConv {conviction}/5"
        if isinstance(conviction, int) and 1 <= conviction <= 5
        else ""
    )
    extras_part = ""
    if extras:
        extras_part = "\n" + " | ".join(
            f"{k} {_word_truncate(str(v), 120)}" for k, v in extras.items() if v
        )
    msg = f"🎯 *{a}* | `{t}`{size_part}\nGrund: {reason_clean}{conv_part}{extras_part}"
    return send_notification(msg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    send_notification("🤖 Trading Advisor bot is now running!")
