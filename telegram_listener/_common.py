"""Shared utilities for telegram_listener handlers — auth, decorator, parsers,
constants. Imported by every handler module."""

import functools
import logging
import os
import re

from telegram import Update
from telegram.ext import ContextTypes

import config

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


_TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}(?:\.[A-Z]{1,3})?$")


def _authorized(update: Update) -> bool:
    """Only accept commands from the configured chat."""
    if not update.effective_chat or not TELEGRAM_CHAT_ID:
        return False
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)


def telegram_handler(fn):
    """Wrap a CommandHandler coroutine with two safety nets:

    1. Null-message guard: Telegram delivers Update objects without `.message`
       for edited_message / channel_post / reactions / inline_query. Without
       this guard, calling `update.message.reply_text(...)` AttributeErrors
       silently. (Bug 2026-04-28: /add ran twice because first call crashed
       on `update.message=None` after restart-buffered update, leaving state
       partially mutated and user with no Telegram feedback.)
    2. Generic exception → reply: surface crashes to the chat instead of
       silent log-only failure. State changes BEFORE the crash already saved
       (handlers commit under portfolio_lock) — user needs to know.
    """
    @functools.wraps(fn)
    async def wrapped(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.message is None:
            logger.debug(
                "Update without .message in %s (effective_message type=%s) — skipped",
                fn.__name__,
                type(update.effective_message).__name__ if update.effective_message else "None",
            )
            return
        try:
            return await fn(update, ctx)
        except Exception as e:
            logger.exception("Handler %s crashed", fn.__name__)
            try:
                cmd = fn.__name__.removesuffix("_handler")
                await update.message.reply_text(
                    f"⚠️ Internal error in /{cmd}: `{type(e).__name__}: {e}`\n"
                    "_State-Änderungen vor dem Crash sind bereits gespeichert. Log prüfen._",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
    return wrapped


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


_WATCH_TYPES = {"breakout_long", "support_bounce", "resistance_reject", "inverse_etf_entry"}



def _parse_dividend_args(args: list[str]) -> tuple[str | None, float | None, str]:
    """Parse `/dividend TICKER AMOUNT [reason...]`. Order-tolerant for ticker/amount."""
    ticker = None
    amount = None
    reason_tokens: list[str] = []
    for tok in args:
        if amount is None and _NUMBER_RE.match(tok):
            try:
                amount = float(tok)
                continue
            except ValueError:
                pass
        if ticker is None:
            upper = tok.upper()
            if _TICKER_RE.match(upper) and any(c.isalpha() for c in upper):
                ticker = upper
                continue
        reason_tokens.append(tok)
    return ticker, amount, " ".join(reason_tokens).strip()
