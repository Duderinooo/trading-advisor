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

        # Correlation pre-warning: gate at analyzer-time only blocks at
        # MAX_CORRELATED_HOLDINGS+1 holdings ≥ MAX_CORRELATION. Pass-through
        # recs can still carry single high-corr pairs the user should see
        # *before* doubling exposure to the same factor. Warn-only — user
        # decides whether the thesis justifies the concentration.
        correlations = rec.get("correlations") or {}
        if isinstance(correlations, dict) and correlations:
            high = sorted(
                ((t, c) for t, c in correlations.items()
                 if isinstance(c, (int, float)) and c >= 0.5),
                key=lambda x: -x[1],
            )
            if high:
                warn_lines = ["⚠️ *Correlation hinweis*"]
                for t, c in high[:3]:
                    warn_lines.append(f"  • {t}: ρ={c:.2f}")
                warn_lines.append(
                    "_ρ ≥ 0.7 mit ≥2 Holdings würde gates-blocken; "
                    "hier nur info._"
                )
                await update.message.reply_text(
                    "\n".join(warn_lines), parse_mode="Markdown",
                )

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

        # Common base shape (shared with paper-portfolio auto-open) +
        # confirm-only fields (slippage, watch_thesis, confluence-snapshot, etc.).
        trade = build_trade_dict(
            rec, entry, shares,
            entry_snapshot=entry_snapshot, paper=False,
        )
        trade.update({
            "watch_thesis": rec.get("watch_thesis"),
            "confluence_score": rec.get("confluence_score"),
            "confluence_items": rec.get("confluence_items"),
            "correlations": rec.get("correlations"),
            "auto_split_tp": rec.get("auto_split_tp"),
            "dd_soft_scale": rec.get("dd_soft_scale"),
            "vix_dampener": rec.get("vix_dampener"),
            "kelly_clamp": rec.get("kelly_clamp"),
            "rec_entry_price": rec_entry,
            "slippage_pct": round(slippage_pct, 3),
            "entry_fee_eur": config.FIXED_FEE_EUR_PER_SIDE,
        })

        portfolio.setdefault("open_trades", []).append(trade)
        # Entry-Fee mitbuchen (2026-05-21). User zahlt €1 an TR pro Order — Bot-Cash
        # spiegelt das jetzt, sonst überschätzt Cash-Stand systematisch.
        portfolio["cash_eur"] = round(cash - actual_size - config.FIXED_FEE_EUR_PER_SIDE, 2)
        pending.pop(rec_idx)
        portfolio["pending_recommendations"] = pending

        # Refresh correlation snapshot when crossing the 2-position threshold so
        # the dashboard heatmap fills in immediately instead of waiting for the
        # next morning brief (Bug 2026-05-02: user opened 2nd position, heatmap
        # stayed empty until 08:00 next day).
        if len(portfolio["open_trades"]) >= 2:
            try:
                from core.metrics import compute_correlation_snapshot
                from core.portfolio.runtime_store import update_runtime
                snap = compute_correlation_snapshot(portfolio)
                if snap is not None:
                    update_runtime({"correlation_matrix": snap})
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
