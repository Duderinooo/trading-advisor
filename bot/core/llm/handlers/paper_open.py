"""Paper-portfolio mirror: open a passing entry-rec into the paper book.

No Telegram, no MemPalace — paper trail stays out of user-facing history.
Same shape as /confirm but auto, no slippage, €1 fee. Fail-soft: never blocks
the real flow.
"""

import logging

import config


logger = logging.getLogger(__name__)


def auto_paper_open(rec: dict) -> None:
    """Mirror a passing entry-rec into the paper portfolio for parallel learning."""
    from core.portfolio import (
        load_paper_portfolio, save_paper_portfolio, paper_lock, build_trade_dict,
    )
    entry = float(rec.get("entry_price") or 0)
    size = float(rec.get("size_eur") or 0)
    shares = int(size / entry) if entry > 0 else 0
    if shares < 1:
        return
    fee = config.FIXED_FEE_EUR_PER_SIDE
    with paper_lock:
        pp = load_paper_portfolio()
        cost = shares * entry + fee
        cash = float(pp.get("cash_eur", 0) or 0)
        if cost > cash + 0.01:
            logger.info("paper: skip %s (cash €%.2f < cost €%.2f)",
                        rec.get("ticker"), cash, cost)
            return
        trade = build_trade_dict(
            rec, entry, float(shares), entry_snapshot=None, paper=True,
        )
        trade["entry_fee_eur"] = fee
        pp["cash_eur"] = round(cash - cost, 2)
        pp.setdefault("open_trades", []).append(trade)
        save_paper_portfolio(pp)
        logger.info(
            "paper: opened %s %d×€%.2f fee=€%.2f cash_now=€%.2f",
            trade["ticker"], shares, entry, fee, pp["cash_eur"],
        )
