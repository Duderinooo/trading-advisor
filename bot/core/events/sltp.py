"""Stop-loss / take-profit / trailing-stop loop + close helpers.

check_stop_loss_take_profit runs on real + paper portfolios. Handles:
- trailing-stop ratchet
- full SL hit → close + free cash
- TP hit: partial close at TP1 (PARTIAL_TP_FRACTION × shares), BE-shift on
  remainder with fee-buffer, 1.5×ATR trailing activation; full close at final TP
- approaching-SL warning (no state change, alert only)

Fees: TR €1/side charged on every real + paper exit so bot-cash mirrors user's
actual TR fees (2026-05-21 — without this, PnL was systematically overstated).
"""

import logging
from datetime import datetime

import config
from core.events.types import EventType
from core.data.market_data import get_market_data
from core.portfolio import (
    portfolio_lock, load_portfolio, save_portfolio, maintain_drawdown_state,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trade-state helpers
# ---------------------------------------------------------------------------

def _next_take_profit(trade: dict) -> float | None:
    """Next TP target. Supports scalar or list (gestaffelt)."""
    tp = trade.get("take_profit")
    if tp is None:
        return None
    if isinstance(tp, list):
        return float(tp[0]) if tp else None
    return float(tp)


def _pop_first_take_profit(trade: dict) -> None:
    """Consume the first TP in a list; scalar TP → None."""
    tp = trade.get("take_profit")
    if isinstance(tp, list):
        tp.pop(0)
        if not tp:
            trade["take_profit"] = None
    else:
        trade["take_profit"] = None


def _apply_trailing_stop(trade: dict, current_price: float) -> bool:
    """Ratchet stop-loss up based on `trailing_stop_pct`. Never moves stop down."""
    trail_pct = trade.get("trailing_stop_pct")
    if not trail_pct or trail_pct <= 0:
        return False
    candidate = current_price * (1 - trail_pct / 100)
    current_stop = trade.get("stop_loss")
    if current_stop is None or candidate > current_stop:
        trade["stop_loss"] = round(candidate, 2)
        return True
    return False


def _close_partial(
    trade: dict, shares_to_sell: float, exit_price: float,
    reason: str, portfolio: dict,
) -> dict:
    """Sell `shares_to_sell` shares of an open trade at exit_price. Reduces
    trade.shares on remainder. Records the partial as its own closed_trades
    entry with `partial=True`. Frees cash minus fee. Does NOT touch SL/TP —
    caller handles (e.g. BE-shift). Returns the closed-partial dict."""
    entry = float(trade.get("entry_price", 0) or 0)
    pnl_eur = (exit_price - entry) * shares_to_sell if entry else 0.0
    pnl_pct = ((exit_price - entry) / entry * 100) if entry else 0.0

    partial = dict(trade)
    partial.update({
        "shares": round(shares_to_sell, 4),
        "exit_price": exit_price,
        "exit_reason": reason,
        "exit_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pnl_eur": round(pnl_eur, 2),
        "pnl_pct": round(pnl_pct, 2),
        "status": "closed_partial",
        "partial": True,
    })
    # Brier on partial: only score the FIRST close (TP1 hit). Later partials
    # would double-count.
    seq = (trade.get("partial_seq") or 0) + 1
    trade["partial_seq"] = seq
    p_win = trade.get("p_win")
    if seq == 1 and isinstance(p_win, (int, float)) and 0.0 <= p_win <= 1.0:
        outcome = 1 if pnl_pct > 0 else 0
        partial["brier"] = round((p_win - outcome) ** 2, 4)
        partial["outcome"] = outcome

    fee = config.FIXED_FEE_EUR_PER_SIDE
    portfolio.setdefault("closed_trades", []).append(partial)
    portfolio["cash_eur"] = round(
        portfolio.get("cash_eur", 0) + (exit_price * shares_to_sell) - fee, 2,
    )
    partial["exit_fee_eur"] = fee

    remaining = round(float(trade.get("shares", 0) or 0) - shares_to_sell, 4)
    trade["shares"] = max(remaining, 0.0)
    trade["size_eur"] = round(trade["shares"] * entry, 2) if entry else 0.0
    # Lifecycle: partial_seq ≥1 → status partial_exit. Aggregate partial PnL.
    trade["status"] = "partial_exit"
    trade["partial_close_count"] = int(trade.get("partial_close_count") or 0) + 1
    trade["total_partial_pnl_eur"] = round(
        float(trade.get("total_partial_pnl_eur") or 0.0) + pnl_eur, 2,
    )
    return partial


def _close_trade(
    trade: dict, exit_price: float, reason: str, portfolio: dict,
) -> None:
    """Move trade from open_trades to closed_trades with exit metadata.
    Credits cash minus fee (assuming user executes on TR; SL/TP mirrored by broker)."""
    entry = trade.get("entry_price", 0)
    shares = trade.get("shares", 0)
    pnl_eur = (exit_price - entry) * shares if entry and shares else 0
    pnl_pct = ((exit_price - entry) / entry * 100) if entry else 0

    exit_type_map = {
        "STOP_LOSS": "sl_hit",
        "TAKE_PROFIT": "tp_hit",
        "TAKE_PROFIT_PARTIAL": "tp_partial",
        "MANUAL_CLOSE": "manual",
        "THESIS_BREAK": "thesis_break",
    }
    exit_type = exit_type_map.get(reason, "other")

    # R-Multiple realized: (exit−entry) / initial_risk_per_share. LONG-only.
    irs = trade.get("initial_risk_per_share")
    realized_r = (
        round((exit_price - entry) / irs, 3)
        if isinstance(irs, (int, float)) and irs > 0
        else None
    )

    holding_days = None
    entry_date_str = trade.get("entry_date")
    if entry_date_str:
        try:
            entry_dt = datetime.strptime(entry_date_str, "%Y-%m-%d %H:%M")
            holding_days = round((datetime.now() - entry_dt).total_seconds() / 86400, 2)
        except ValueError:
            pass

    closed = dict(trade)
    closed.update({
        "exit_price": exit_price,
        "exit_reason": reason,
        "exit_type": exit_type,
        "exit_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pnl_eur": round(pnl_eur, 2),
        "pnl_pct": round(pnl_pct, 2),
        "realized_r": realized_r,
        "holding_days_realized": holding_days,
        "status": "closed",
    })

    # Auto-tag execution slippage on SL: filled ≥SL_SLIPPAGE_TAG_PERCENT below
    # nominal SL → execution error (fast-market gap or bad fill, not thesis
    # failure). Surfaces execution-class dominance even without user /close #tag.
    if reason == "STOP_LOSS" and pnl_pct <= 0:
        sl = trade.get("stop_loss")
        if sl and sl > 0:
            slip_below = (sl - exit_price) / sl * 100
            if slip_below >= config.SL_SLIPPAGE_TAG_PERCENT:
                closed["mistake_tag"] = "slippage"
                closed["mistake_class"] = "execution"
                closed["sl_exit_slippage_pct"] = round(slip_below, 3)
                logger.warning(
                    "Auto-tag slippage: %s exit €%.2f vs SL €%.2f (%.2f%% below)",
                    trade.get("ticker", "?"), exit_price, sl, slip_below,
                )
            else:
                closed["mistake_tag"] = None
                closed["mistake_class"] = "untagged"
        else:
            closed["mistake_tag"] = None
            closed["mistake_class"] = "untagged"

    # Brier score: (p_predicted - outcome)^2. outcome=1 if win, 0 if loss.
    p_win = trade.get("p_win")
    if isinstance(p_win, (int, float)) and 0.0 <= p_win <= 1.0:
        outcome = 1 if pnl_pct > 0 else 0
        closed["brier"] = round((p_win - outcome) ** 2, 4)
        closed["outcome"] = outcome

    # Alpha vs Beta attribution (same period as trade hold-window).
    try:
        from core.data.market_data import get_period_return
        spy_ret = get_period_return("SPY5.DE", trade.get("entry_date", ""), closed["exit_date"])
        if spy_ret is not None:
            closed["spy_return_pct"] = spy_ret
            closed["alpha_pct"] = round(pnl_pct - spy_ret, 2)
    except Exception:
        logger.exception("SPY-attribution failed for %s", trade.get("ticker", "?"))

    fee = config.FIXED_FEE_EUR_PER_SIDE
    portfolio.setdefault("closed_trades", []).append(closed)
    portfolio["cash_eur"] = round(
        portfolio.get("cash_eur", 0) + (exit_price * shares) - fee, 2,
    )
    closed["exit_fee_eur"] = fee


# ---------------------------------------------------------------------------
# Main SL/TP loop
# ---------------------------------------------------------------------------

def check_stop_loss_take_profit(paper: bool = False) -> list[dict]:
    """Check open trades for SL / TP triggers. On full exit (SL or final TP),
    moves trade to closed_trades + frees cash. Handles trailing stops + BE-shift
    after TP1.

    paper=True runs the same logic against training_portfolio.json (own lock,
    own file). Both real + paper book €1/side TR fee on exit (2026-05-21 —
    bot-cash now mirrors user's real TR fees; without this, overstated PnL)."""
    if paper:
        from core.portfolio import (
            load_paper_portfolio, save_paper_portfolio, paper_lock,
        )
        loader, saver, lock = load_paper_portfolio, save_paper_portfolio, paper_lock
    else:
        loader, saver, lock = load_portfolio, save_portfolio, portfolio_lock
    with lock:
        portfolio = loader()
        open_trades = portfolio.get("open_trades", [])
        if not open_trades:
            return []

        alerts: list[dict] = []
        portfolio_dirty = False
        tickers = [t["ticker"] for t in open_trades]
        market_data = get_market_data(tickers)

        surviving_trades: list[dict] = []
        for trade in open_trades:
            ticker = trade["ticker"]
            data = market_data.get(ticker, {})
            if "error" in data:
                surviving_trades.append(trade)
                continue
            current_price = data.get("price")
            if not current_price:
                surviving_trades.append(trade)
                continue

            entry = trade.get("entry_price", 0)

            if _apply_trailing_stop(trade, current_price):
                portfolio_dirty = True
                alerts.append({
                    "type": EventType.TRAILING_STOP_MOVED,
                    "ticker": ticker,
                    "new_stop": trade["stop_loss"],
                    "current_price": current_price,
                })

            stop_loss = trade.get("stop_loss")
            take_profit = _next_take_profit(trade)

            # ---- SL hit → full close ----
            if stop_loss and current_price <= stop_loss:
                pnl_pct = ((current_price - entry) / entry * 100) if entry else 0
                alerts.append({
                    "type": EventType.STOP_LOSS_HIT,
                    "ticker": ticker,
                    "entry": entry,
                    "stop_loss": stop_loss,
                    "current_price": current_price,
                    "pnl_pct": pnl_pct,
                })
                _close_trade(trade, current_price, "STOP_LOSS", portfolio)
                portfolio_dirty = True
                continue

            # ---- TP hit ----
            if take_profit and current_price >= take_profit:
                pnl_pct = ((current_price - entry) / entry * 100) if entry else 0
                had_more_tps = isinstance(trade.get("take_profit"), list) and len(trade["take_profit"]) > 1

                if had_more_tps:
                    # Partial close at TP1. Locks PARTIAL_TP_FRACTION × shares;
                    # remainder runs with BE-SL + trailing → locks ≥0.5R win
                    # even if runner stops out.
                    shares_total = float(trade.get("shares", 0) or 0)
                    shares_to_sell = round(shares_total * config.PARTIAL_TP_FRACTION, 4)
                    if shares_to_sell > 0 and shares_to_sell < shares_total:
                        _close_partial(trade, shares_to_sell, current_price,
                                       "TAKE_PROFIT_PARTIAL", portfolio)
                        alerts.append({
                            "type": EventType.PARTIAL_TP_HIT,
                            "ticker": ticker,
                            "entry": entry,
                            "take_profit": take_profit,
                            "current_price": current_price,
                            "pnl_pct": pnl_pct,
                            "shares_sold": shares_to_sell,
                            "shares_remaining": trade["shares"],
                        })
                        _pop_first_take_profit(trade)
                        portfolio_dirty = True

                        # BE-Shift with fee-buffer: at €1k capital + €1 TR fee/side,
                        # a nominal BE-exit (€0 gross PnL on remainder) would net to
                        # a loss after sell-fee. Buffer = €1/remaining_shares lifts
                        # SL enough to cover the sell-fee.
                        shares_remaining = float(trade.get("shares", 0) or 0)
                        be_buffer_per_share = (
                            config.FIXED_FEE_EUR_PER_SIDE / shares_remaining
                            if shares_remaining > 0 else 0.0
                        )
                        be_target = round(entry + be_buffer_per_share, 2) if entry else 0
                        if entry and (trade.get("stop_loss") is None or trade["stop_loss"] < be_target):
                            trade["stop_loss"] = be_target
                            alerts.append({
                                "type": EventType.BREAK_EVEN_SHIFT,
                                "ticker": ticker,
                                "new_stop": be_target,
                                "fee_buffer_eur": round(be_buffer_per_share * shares_remaining, 2),
                            })
                        if not trade.get("trailing_stop_pct"):
                            atr_pct = data.get("atr14_pct")
                            if isinstance(atr_pct, (int, float)) and atr_pct > 0:
                                trail_pct = round(atr_pct * 1.5, 2)
                                trade["trailing_stop_pct"] = trail_pct
                                alerts.append({
                                    "type": EventType.TRAILING_ACTIVATED,
                                    "ticker": ticker,
                                    "trail_pct": trail_pct,
                                    "atr_pct": atr_pct,
                                })
                        if trade.get("shares", 0) > 0:
                            surviving_trades.append(trade)
                    else:
                        # Edge: fractional share too small to split. Close full
                        # position rather than leave a phantom open trade.
                        logger.warning(
                            "Partial-TP fraction collapsed (shares_total=%s, "
                            "shares_to_sell=%s); closing full position",
                            shares_total, shares_to_sell,
                        )
                        alerts.append({
                            "type": EventType.TAKE_PROFIT_HIT,
                            "ticker": ticker,
                            "entry": entry,
                            "take_profit": take_profit,
                            "current_price": current_price,
                            "pnl_pct": pnl_pct,
                            "partial": False,
                        })
                        _pop_first_take_profit(trade)
                        _close_trade(trade, current_price, "TAKE_PROFIT", portfolio)
                        portfolio_dirty = True
                else:
                    # Final TP — close full remainder.
                    alerts.append({
                        "type": EventType.TAKE_PROFIT_HIT,
                        "ticker": ticker,
                        "entry": entry,
                        "take_profit": take_profit,
                        "current_price": current_price,
                        "pnl_pct": pnl_pct,
                        "partial": False,
                    })
                    _pop_first_take_profit(trade)
                    _close_trade(trade, current_price, "TAKE_PROFIT", portfolio)
                    portfolio_dirty = True
                continue

            # ---- Approaching-SL warning ----
            if stop_loss and current_price <= stop_loss * (1 + config.SL_WARN_DISTANCE_PCT / 100):
                alerts.append({
                    "type": EventType.STOP_LOSS_WARNING,
                    "ticker": ticker,
                    "stop_loss": stop_loss,
                    "current_price": current_price,
                    "distance_pct": ((current_price - stop_loss) / stop_loss * 100),
                })

            surviving_trades.append(trade)

        if portfolio_dirty:
            portfolio["open_trades"] = surviving_trades
            # Recompute DD halt latch — SL/TP hits just changed realized equity.
            # Paper has its own DD trail (for stats) but halt blocks nothing
            # (auto-open runs through analyzer, ignores paper-DD).
            maintain_drawdown_state(portfolio)
            saver(portfolio)

        return alerts
