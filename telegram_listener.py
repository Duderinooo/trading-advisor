"""Telegram command listener: manual trade confirmation + close + portfolio queries.

Runs in a background thread with its own asyncio loop, so the main event-check
loop is never blocked. All portfolio mutations acquire `portfolio_lock`.
"""

import os
import re
import logging
import threading
import asyncio
import functools
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import config
from core import (
    portfolio_lock, load_portfolio, save_portfolio, add_cash_movement,
    get_market_data,
    risk_halt_status, set_kill_switch, kill_switch_active,
    maintain_drawdown_state, compute_slippage_budget, get_period_return,
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
    """Hard-wipe ALL watch-levels. Confirm with arg 'yes'."""
    if not _authorized(update):
        return
    args = ctx.args or []
    if not args or args[0].lower() != "yes":
        await update.message.reply_text(
            "⚠️ `/watchclear yes` löscht ALLE Watchlevels. Bestätigung nötig.",
            parse_mode="Markdown",
        )
        return
    with portfolio_lock:
        portfolio = load_portfolio()
        n = len(portfolio.get("watch_levels", []))
        portfolio["watch_levels"] = []
        save_portfolio(portfolio)
    await update.message.reply_text(f"🗑️ Alle {n} Watchlevels entfernt.")


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


async def _handle_update_confirm(
    update: Update,
    rec: dict,
    rec_idx: int,
    pending: list,
    portfolio: dict,
) -> None:
    """Apply Claude-recommended SL/TP update to existing open_trade. Caller holds lock."""
    ticker = (rec.get("ticker") or "").upper()
    open_trade = next(
        (t for t in portfolio.get("open_trades", [])
         if (t.get("ticker") or "").upper() == ticker),
        None,
    )
    if open_trade is None:
        pending.pop(rec_idx)
        portfolio["pending_recommendations"] = pending
        save_portfolio(portfolio)
        await update.message.reply_text(
            f"❌ UPDATE verworfen: keine offene Position für {ticker} mehr.",
        )
        return

    new_sl = rec.get("new_stop_loss")
    new_tp = rec.get("new_take_profit")
    if new_sl is None and new_tp is None:
        await update.message.reply_text(
            f"❌ UPDATE leer: weder neuer SL noch TP gesetzt für {ticker}.",
        )
        return

    old_sl = open_trade.get("stop_loss")
    old_tp = open_trade.get("take_profit")
    if new_sl is not None:
        open_trade["stop_loss"] = float(new_sl)
    if new_tp is not None:
        # Normalize to list[float] for consistency with TP1/TP2 logic
        open_trade["take_profit"] = (
            [float(x) for x in new_tp] if isinstance(new_tp, list) else [float(new_tp)]
        )
    open_trade.setdefault("update_history", []).append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "old_sl": old_sl,
        "new_sl": new_sl,
        "old_tp": old_tp,
        "new_tp": new_tp,
        "reason": rec.get("reason"),
    })

    pending.pop(rec_idx)
    portfolio["pending_recommendations"] = pending
    save_portfolio(portfolio)

    if MEMPALACE_AVAILABLE:
        try:
            log_trade(open_trade, "UPDATED", rec.get("reason", ""))
        except Exception:
            logger.exception("MemPalace log_trade UPDATE failed")

    new_sl_str = f"€{float(new_sl):.2f}" if new_sl is not None else "(unverändert)"
    new_tp_str = (
        " / ".join(f"€{t:.2f}" for t in open_trade["take_profit"])
        if isinstance(open_trade["take_profit"], list) else "?"
    )
    old_sl_str = f"€{old_sl:.2f}" if isinstance(old_sl, (int, float)) else "–"
    old_tp_str = (
        " / ".join(f"€{t:.2f}" for t in old_tp) if isinstance(old_tp, list)
        else (f"€{old_tp:.2f}" if isinstance(old_tp, (int, float)) else "–")
    )
    await update.message.reply_text(
        f"🔧 *{ticker} SL/TP aktualisiert*\n"
        f"SL: {old_sl_str} → {new_sl_str}\n"
        f"TP: {old_tp_str} → {new_tp_str}\n"
        f"Grund: _{rec.get('reason', '–')}_",
        parse_mode="Markdown",
    )


async def _handle_exit_confirm(
    update: Update,
    rec: dict,
    rec_idx: int,
    pending: list,
    portfolio: dict,
) -> None:
    """User accepts Claude-recommended exit. We don't auto-close — user has to
    sell on TR first (we don't have broker API). After TR fill, user runs
    /close TICKER @PREIS [#tag]. /confirm here just acknowledges + primes."""
    ticker = (rec.get("ticker") or "").upper()
    open_trade = next(
        (t for t in portfolio.get("open_trades", [])
         if (t.get("ticker") or "").upper() == ticker),
        None,
    )
    if open_trade is None:
        pending.pop(rec_idx)
        portfolio["pending_recommendations"] = pending
        save_portfolio(portfolio)
        await update.message.reply_text(
            f"ℹ️ EXIT-Rec für {ticker} verworfen — Position bereits geschlossen.",
        )
        return

    pending.pop(rec_idx)
    portfolio["pending_recommendations"] = pending
    save_portfolio(portfolio)

    await update.message.reply_text(
        f"✅ *EXIT bestätigt: {ticker}*\n"
        f"Verkaufe jetzt auf TR. Nach Fill: `/close {ticker} @PREIS`\n"
        f"_(optional bei Verlust: `#thesis_wrong / #timing_late / #news_shock` etc.)_",
        parse_mode="Markdown",
    )


async def _handle_add_confirm(
    update: Update,
    rec: dict,
    rec_idx: int,
    pending: list,
    portfolio: dict,
    ticker_arg: str | None,
    price_override: float | None,
    shares_override: float | None,
) -> None:
    """ADD-confirm: pyramid into existing position. Caller holds portfolio_lock."""
    ticker = (rec.get("ticker") or "").upper()
    add_size_eur = float(rec.get("additional_size_eur") or 0)

    open_trade = next(
        (t for t in portfolio.get("open_trades", [])
         if (t.get("ticker") or "").upper() == ticker),
        None,
    )
    if open_trade is None:
        pending.pop(rec_idx)
        portfolio["pending_recommendations"] = pending
        save_portfolio(portfolio)
        await update.message.reply_text(
            f"❌ ADD verworfen: keine offene Position für {ticker} mehr.",
        )
        return

    # Live-pull when no @price (consistent with entry-flow). Avoid stale rec time.
    fill_price: float | None = price_override
    if fill_price is None:
        try:
            live = get_market_data([ticker]).get(ticker, {})
            lp = live.get("price") if isinstance(live, dict) else None
            if isinstance(lp, (int, float)) and lp > 0:
                fill_price = float(lp)
        except Exception:
            logger.exception("ADD live-price fetch failed")
    if not fill_price or fill_price <= 0:
        await update.message.reply_text(
            f"❌ Kein gültiger Preis für ADD {ticker}. `@PREIS` angeben.",
        )
        return

    # Risk-halt re-check at confirm time
    halt = risk_halt_status(portfolio)
    if halt["halt"]:
        await update.message.reply_text(
            "⛔ *Risk-Halt aktiv — ADD blockiert*\n"
            + "\n".join(f"• {r}" for r in halt["reasons"]),
            parse_mode="Markdown",
        )
        return

    if shares_override is not None:
        added_shares = float(shares_override)
        actual_added = round(added_shares * fill_price, 2)
    else:
        added_shares = round(add_size_eur / fill_price, 4) if fill_price > 0 else 0.0
        if added_shares <= 0:
            added_shares = 1.0
        actual_added = round(added_shares * fill_price, 2)

    cash = float(portfolio.get("cash_eur", 0) or 0)
    if actual_added > cash + 0.01:
        max_shares = round(cash / fill_price, 4) if fill_price > 0 else 0
        await update.message.reply_text(
            f"⚠️ Nicht genug Cash für ADD: brauche €{actual_added:.2f}, habe €{cash:.2f}.\n"
            f"Max möglich: {max_shares} Stück.",
        )
        return

    # Weighted-avg entry: aggregates positions so SL/TP math + Brier on aggregate.
    old_shares = float(open_trade.get("shares", 0) or 0)
    old_size = float(open_trade.get("size_eur", 0) or 0)
    old_entry = float(open_trade.get("entry_price", 0) or 0)
    new_shares = round(old_shares + added_shares, 4)
    new_size = round(old_size + actual_added, 2)
    new_entry = round(new_size / new_shares, 4) if new_shares > 0 else fill_price

    open_trade["shares"] = new_shares
    open_trade["size_eur"] = new_size
    open_trade["entry_price"] = new_entry
    open_trade.setdefault("add_history", []).append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "added_shares": added_shares,
        "added_size_eur": actual_added,
        "fill_price": fill_price,
        "trigger": rec.get("trigger"),
        "thesis_reinforcement": rec.get("thesis_reinforcement"),
        "conviction": rec.get("conviction"),
    })

    portfolio["cash_eur"] = round(cash - actual_added, 2)
    pending.pop(rec_idx)
    portfolio["pending_recommendations"] = pending
    save_portfolio(portfolio)

    if MEMPALACE_AVAILABLE:
        try:
            log_trade(open_trade, "ADDED", rec.get("thesis_reinforcement", ""))
        except Exception:
            logger.exception("MemPalace log_trade ADD failed")

    sl = open_trade.get("stop_loss")
    sl_str = f"€{sl:.2f}" if sl else "–"
    tp = open_trade.get("take_profit")
    tp_str = (
        " / ".join(f"€{t:.2f}" for t in tp) if isinstance(tp, list)
        else (f"€{tp:.2f}" if tp else "–")
    )
    await update.message.reply_text(
        f"✅ *{ticker} aufgestockt*\n"
        f"+{added_shares:g} × €{fill_price:.2f} = €{actual_added:.2f}\n"
        f"Neu: {new_shares:g} Stk | Avg-Entry €{new_entry:.4f} | Σ €{new_size:.2f}\n"
        f"SL: {sl_str} | TP: {tp_str}\n"
        f"Cash: €{portfolio['cash_eur']:.2f}",
        parse_mode="Markdown",
    )


@telegram_handler
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

        # TTL: reject stale recs. Price & thesis decay fast intraday; re-analyze.
        rec_ts = rec.get("timestamp")
        if rec_ts:
            try:
                rec_dt = datetime.strptime(rec_ts, "%Y-%m-%d %H:%M")
                age = datetime.now() - rec_dt
                if age > timedelta(hours=config.PENDING_REC_TTL_HOURS):
                    pending.pop(rec_idx)
                    portfolio["pending_recommendations"] = pending
                    save_portfolio(portfolio)
                    await update.message.reply_text(
                        f"⏱️ *Rec veraltet* ({rec.get('ticker')})\n"
                        f"Alter: {age.total_seconds()/3600:.1f}h > {config.PENDING_REC_TTL_HOURS}h.\n"
                        f"Verworfen. Neue Analyse abwarten.",
                        parse_mode="Markdown",
                    )
                    return
            except ValueError:
                pass

        # ADD-Flow: pyramiding into existing position. Weighted-avg entry, no new
        # open_trades entry, original SL/TP preserved. Slippage gate is skipped
        # because ADD has no rec_entry baseline (Claude says "add at market").
        if rec.get("kind") == "add":
            await _handle_add_confirm(
                update, rec, rec_idx, pending, portfolio,
                ticker_arg, price_override, shares_override,
            )
            return

        # UPDATE-Flow: Sonnet/Haiku raised SL or TP based on new market info.
        # Apply directly to open_trade. No fill required.
        if rec.get("kind") == "update":
            await _handle_update_confirm(update, rec, rec_idx, pending, portfolio)
            return

        # EXIT-Flow: Claude-recommended exit. Doesn't actually close (user closes
        # on TR + sends /close). Just removes the rec from pending and replies
        # with the /close command primed.
        if rec.get("kind") == "exit":
            await _handle_exit_confirm(update, rec, rec_idx, pending, portfolio)
            return

        rec_entry = float(rec.get("entry_price", 0) or 0)
        # Live-price pull when user didn't supply @price.
        # Why: 15min-delayed yfinance is closer to actual fill than rec_entry from
        # hours ago. Forces slippage gate to run instead of silently recording rec_entry.
        price_source = "user"
        live: dict | None = None
        if price_override is None:
            try:
                live = get_market_data([rec["ticker"]]).get(rec["ticker"], {})
                live_price = live.get("price") if isinstance(live, dict) else None
                if isinstance(live_price, (int, float)) and live_price > 0:
                    price_override = float(live_price)
                    price_source = "live"
            except Exception:
                logger.exception("Live-price fetch failed at /confirm")
        entry = price_override if price_override is not None else rec_entry
        if entry <= 0:
            await update.message.reply_text("❌ Kein gültiger Entry-Preis. `@PREIS` angeben.")
            return

        # Slippage gate: adaptive budget from rolling 30-trade avg slippage.
        # Why: fills tighten/loosen with liquidity regime. Static gate over-blocks
        # in calm tape and under-blocks in volatile tape.
        slip_budget = compute_slippage_budget(portfolio.get("closed_trades", []))
        slippage_pct = 0.0
        if price_override is not None and rec_entry > 0:
            slippage_pct = (entry - rec_entry) / rec_entry * 100
            if abs(slippage_pct) > slip_budget:
                src_note = "live (15min delayed)" if price_source == "live" else "Fill"
                await update.message.reply_text(
                    f"⛔ *Slippage-Abbruch*\n"
                    f"Rec €{rec_entry:.2f} vs. {src_note} €{entry:.2f} "
                    f"({slippage_pct:+.2f}%) > {slip_budget}% (adaptiv).\n"
                    f"Mit echtem Fill quoten: `/confirm @PREIS` oder `/cancel`.",
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

        # Thesis-state snapshot at entry: freezes analyst consensus + structural
        # markers so events.py can detect thesis-degradation later (analyst downgrade,
        # MA50-loss, wk_trend flip). Without this, "is the thesis still intact?" is
        # not answerable mid-trade.
        snapshot_data = live if isinstance(live, dict) and live and not live.get("error") else {}
        if not snapshot_data:
            try:
                _snap = get_market_data([rec["ticker"]]).get(rec["ticker"], {})
                if isinstance(_snap, dict) and not _snap.get("error"):
                    snapshot_data = _snap
            except Exception:
                logger.exception("Snapshot fetch failed at /confirm")
        entry_snapshot = {
            "snapshot_date": datetime.now().strftime("%Y-%m-%d"),
            "analyst_rec_key": snapshot_data.get("analyst_rec_key"),
            "analyst_target_mean": snapshot_data.get("analyst_target_mean"),
            "analyst_upside_pct": snapshot_data.get("analyst_upside_pct"),
            "analyst_count": snapshot_data.get("analyst_count"),
            "rsi14": snapshot_data.get("rsi14"),
            "ma50": snapshot_data.get("ma50"),
            "ma200": snapshot_data.get("ma200"),
            "wk_trend": snapshot_data.get("wk_trend"),
            "rs_20d_vs_index_pct": snapshot_data.get("rs_20d_vs_index_pct"),
        }

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
            "watch_thesis": rec.get("watch_thesis"),
            "entry_snapshot": entry_snapshot,
            "hold_days_min": rec.get("hold_days_min"),
            "hold_days_max": rec.get("hold_days_max"),
            "setup_type": rec.get("setup_type"),
            "top_fail_mode": rec.get("top_fail_mode"),
            "confluence_score": rec.get("confluence_score"),
            "confluence_items": rec.get("confluence_items"),
            "correlations": rec.get("correlations"),
            "auto_split_tp": rec.get("auto_split_tp"),
            "dd_soft_scale": rec.get("dd_soft_scale"),
            "vix_dampener": rec.get("vix_dampener"),
            "kelly_clamp": rec.get("kelly_clamp"),
            "regime_at_entry": rec.get("regime_at_entry"),
            "vix_at_entry": rec.get("vix_at_entry"),
            "entry_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status": "open",
            "rec_entry_price": rec_entry,
            "slippage_pct": round(slippage_pct, 3),
            # MAE/MFE seeded at entry; updated each heartbeat tick by main.py.
            "mae": round(entry, 4),
            "mfe": round(entry, 4),
        }

        portfolio.setdefault("open_trades", []).append(trade)
        portfolio["cash_eur"] = round(cash - actual_size, 2)
        pending.pop(rec_idx)
        portfolio["pending_recommendations"] = pending

        # Refresh correlation snapshot when crossing the 2-position threshold so
        # the dashboard heatmap fills in immediately instead of waiting for the
        # next morning brief (Bug 2026-05-02: user opened 2nd position, heatmap
        # stayed empty until 08:00 next day).
        if len(portfolio["open_trades"]) >= 2:
            try:
                from core.analyzer import compute_correlation_snapshot
                snap = compute_correlation_snapshot(portfolio)
                if snap is not None:
                    portfolio["correlation_matrix"] = snap
            except Exception:
                logger.exception("Correlation snapshot refresh failed at /confirm")

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
    price_note = ""
    if price_source == "live":
        price_note = "\n_⚠️ Preis live-gepulled (15min delayed). Bei tatsächlichem TR-Fill korrigieren._"
    await update.message.reply_text(
        f"✅ *{trade['ticker']} im Portfolio*\n"
        f"{shares:g} × €{entry:.2f} = €{actual_size:.2f}\n"
        f"SL: {sl_str} | TP: {tp_str}\n"
        f"Cash: €{new_cash:.2f}"
        f"{price_note}",
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


@telegram_handler
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


@telegram_handler
async def resume_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    set_kill_switch(False)
    # If morning prep got skipped today by the kill-switch gate, watch_levels stays
    # empty until tomorrow 08:00 unless user re-runs it. Hint so day isn't wasted.
    pf = load_portfolio()
    today = datetime.now().strftime("%Y-%m-%d")
    morning_done = pf.get("last_morning_prep_date") == today
    levels = pf.get("watch_levels", []) or []
    msg = "✅ Kill-Switch AUS. Trading wieder aktiv."
    if morning_done and not levels:
        msg += "\n\n💡 Tipp: `/morning` um Watchlevels zu seedlen — sonst bleibt Tag ohne Setups (Auto-Morgen läuft erst morgen 08:00)."
    await update.message.reply_text(msg)


@telegram_handler
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


@telegram_handler
async def morning_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manual re-run of morning prep (re-populates watch_levels)."""
    if not _authorized(update):
        return
    await update.message.reply_text("⏳ Morning Prep läuft (Sonnet-Call, ~10s)...")
    try:
        # Lazy import to avoid circular deps with main at module load.
        from main import run_morning_prep
        # Clear daily-dedup flag so run_morning_prep doesn't short-circuit.
        with portfolio_lock:
            pf = load_portfolio()
            pf.pop("last_morning_prep_date", None)
            save_portfolio(pf)
        # force=True bypasses kill-switch gate — manual /morning is intentional.
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_morning_prep(force=True)
        )
        pf = load_portfolio()
        levels = pf.get("watch_levels", [])
        tr = pf.get("last_morning_trace") or {}
        # Detect skipped analyze_portfolio (daily-cap etc.): trace ts older than 90s.
        tr_fresh = False
        if tr.get("ts"):
            try:
                from datetime import datetime as _dt
                age = (_dt.now() - _dt.strptime(tr["ts"], "%Y-%m-%d %H:%M:%S")).total_seconds()
                tr_fresh = age < 90
            except Exception:
                pass
        lines = [f"✅ Morning Prep fertig. {len(levels)} Watch-Level gesetzt."]
        if levels:
            tickers = ", ".join((lvl.get("ticker") or "?") for lvl in levels[:8])
            lines.append(tickers)
        if not tr_fresh:
            lines.append(
                "⚠️ Kein frischer Trace — analyze_portfolio übersprungen "
                "(daily-cap? check bot.log)."
            )
        elif tr.get("malformed_tool_input") or tr.get("truncated"):
            lines.append("⚠️ Trace zeigt Problem — bot.log + portfolio.json checken.")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.exception("Manual morning prep failed")
        await update.message.reply_text(f"❌ Morning Prep Fehler: {e}")


@telegram_handler
async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "*Befehle:*\n"
        "`/confirm` (reply) — Empfehlung übernehmen, shares auto-berechnet\n"
        "`/confirm 3` (reply) — 3 Stück, rec-Preis\n"
        "`/confirm 3 @172.50` (reply) — 3 Stück, Preis €172.50\n"
        "`/confirm NVD.DE 3 @172.50` — standalone\n"
        "`/add TICKER STK X @PREIS` — manuell aufstocken (z.B. `/add RWE.DE STK 2 @61.60`)\n"
        "`/watch TICKER TYPE @PREIS [thesis]` — Watchlevel manuell setzen\n"
        "`/watchlist` — alle aktiven Watchlevels anzeigen\n"
        "`/watchremove TICKER` — Watchlevel(s) für Ticker entfernen\n"
        "`/watchclear yes` — ALLE Watchlevels löschen\n"
        "`/close TICKER [@preis] [#tag]` — Position schließen (bei Verlust: #tag = Grund)\n"
        "`/dividend TICKER AMOUNT [grund]` — Dividende verbuchen (Cash + Equity-Curve)\n"
        "`/cancel` (reply) — Pending-Empfehlung verwerfen\n"
        "`/positions` — Portfolio anzeigen\n"
        "`/morning` — Morning Prep manuell neu laufen lassen\n"
        "`/panic [grund]` — Kill-Switch AN (blockt neue Entries + Event-Analysen)\n"
        "`/resume` — Kill-Switch AUS\n"
        "`/killstatus` — Kill-Switch Status",
        parse_mode="Markdown",
    )


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
