"""Cash flow / dividend / pnl helpers (operate on already-loaded portfolio dicts)."""

import logging

logger = logging.getLogger(__name__)


def trade_dividends(trade: dict, cash_movements: list[dict]) -> list[dict]:
    """All dividend cash_movements linked to this trade (matched on composite key).

    Match: ticker + entry_date both equal. exit_date matched too if both present
    (closed trades). Open trades match on null exit_date.
    """
    if not cash_movements:
        return []
    t_ticker = (trade.get("ticker") or "").upper()
    t_entry = trade.get("entry_date")
    t_exit = trade.get("exit_date")
    out = []
    for m in cash_movements:
        if m.get("kind") != "dividend":
            continue
        link = m.get("linked_trade") or {}
        if (link.get("ticker") or "").upper() != t_ticker:
            continue
        if link.get("entry_date") != t_entry:
            continue
        if link.get("exit_date") != t_exit:
            continue
        out.append(m)
    return out


def effective_pnl_eur(trade: dict, cash_movements: list[dict] | None = None) -> float:
    """Trade pnl_eur INCL. linked dividends.

    Why: bot's pnl_eur stores price-component only (exit×shares − entry×shares).
    Dividends paid during hold-period are real cashflow that user earned from
    the trade — must be credited to R-Multiple/Brier so stats reflect reality
    (Bug 2026-05-07: RWE.DE -€13.50 + €7.20 div was scored as -€13.50, hit-stats
    distorted).
    """
    base = float(trade.get("pnl_eur") or 0)
    divs = sum(float(m.get("amount") or 0) for m in trade_dividends(trade, cash_movements or []))
    return round(base + divs, 2)


def effective_pnl_pct(trade: dict, cash_movements: list[dict] | None = None) -> float:
    """Trade pnl_pct INCL. dividends. Recomputed from effective_pnl_eur / size_eur."""
    base_pct = float(trade.get("pnl_pct") or 0)
    divs = sum(float(m.get("amount") or 0) for m in trade_dividends(trade, cash_movements or []))
    if not divs:
        return base_pct
    size_eur = float(trade.get("size_eur") or 0)
    if size_eur <= 0:
        # Fallback: reconstruct size from entry × shares.
        entry = float(trade.get("entry_price") or 0)
        shares = float(trade.get("shares") or 0)
        size_eur = entry * shares
    if size_eur <= 0:
        return base_pct  # Nothing reasonable to scale by.
    return round(base_pct + (divs / size_eur * 100), 2)
