"""Portfolio-level exposure stats: heat (summed entry-SL risk) + sector + equity curve.

heat = open positions' summed (entry - SL) × shares — total nominal risk if all
       SLs hit simultaneously.
sector_exposure = positions grouped by config.SECTOR_MAP; cluster cap enforced
       elsewhere via gate_sector.
equity_stats = realized + cash-movements + drawdown + max-DD over the closed-trade
       history. Drives the dashboard equity curve + DD-halt detection.
"""

import logging
from datetime import datetime

import config
from core.portfolio.cash_movements import effective_pnl_eur


logger = logging.getLogger(__name__)


def compute_portfolio_heat(portfolio: dict) -> dict:
    """Portfolio-level risk: total € at risk if all stops hit simultaneously."""
    open_trades = portfolio.get("open_trades", [])
    capital = float(portfolio.get("total_capital_eur", config.BUDGET_EUR) or config.BUDGET_EUR)

    max_per_trade = capital * config.MAX_RISK_PER_TRADE_PERCENT / 100
    max_budget_eur = max_per_trade * config.MAX_ACTIVE_TRADES

    by_pos = []
    total_heat = 0.0
    warnings: list[str] = []
    for t in open_trades:
        ticker = t.get("ticker", "?")
        entry = float(t.get("entry_price", 0) or 0)
        stop = float(t.get("stop_loss", 0) or 0)
        shares = float(t.get("shares", 0) or 0)

        if entry <= 0 or shares <= 0:
            warnings.append(f"{ticker}: ungültige Entry/Shares")
            risk = 0.0
        elif stop <= 0:
            warnings.append(f"{ticker}: KEIN SL gesetzt — voller Positionswert als Risk")
            risk = entry * shares
        elif stop >= entry:
            warnings.append(f"{ticker}: SL ≥ Entry (ungültig für Long)")
            risk = 0.0
        else:
            risk = (entry - stop) * shares

        total_heat += risk
        by_pos.append({"ticker": ticker, "risk_eur": round(risk, 2)})

    return {
        "positions": len(open_trades),
        "max_positions": config.MAX_ACTIVE_TRADES,
        "total_heat_eur": round(total_heat, 2),
        "heat_pct": round(total_heat / capital * 100, 2) if capital else 0,
        "max_per_trade_eur": round(max_per_trade, 2),
        "max_budget_eur": round(max_budget_eur, 2),
        "remaining_eur": round(max_budget_eur - total_heat, 2),
        "by_position": by_pos,
        "warnings": warnings,
    }


def format_portfolio_heat(heat: dict) -> str:
    header = (
        f"Positionen: {heat['positions']}/{heat['max_positions']} | "
        f"Heat: €{heat['total_heat_eur']:.2f} ({heat['heat_pct']:.2f}% Kapital) | "
        f"Budget remaining: €{heat['remaining_eur']:.2f} (von €{heat['max_budget_eur']:.2f})"
    )
    if heat["positions"] == 0:
        return header
    per_pos = " | ".join(f"{p['ticker']} €{p['risk_eur']:.2f}" for p in heat["by_position"])
    result = f"{header}\nPer Position: {per_pos}"
    if heat.get("warnings"):
        result += "\n⚠️ " + " | ".join(heat["warnings"])
    return result


# ---------- Sector exposure (cluster risk) ----------

def compute_sector_exposure(portfolio: dict) -> dict[str, list[str]]:
    """Group open trades by sector (via config.SECTOR_MAP). Unknown tickers → 'other'."""
    by_sector: dict[str, list[str]] = {}
    for t in portfolio.get("open_trades", []):
        ticker = t.get("ticker", "")
        sector = config.SECTOR_MAP.get(ticker, "other")
        by_sector.setdefault(sector, []).append(ticker)
    return by_sector


def format_sector_exposure(by_sector: dict[str, list[str]]) -> str:
    if not by_sector:
        return "Keine offenen Positionen."
    lines = []
    for sector, tickers in sorted(by_sector.items(), key=lambda x: -len(x[1])):
        flag = " ⚠️ LIMIT" if len(tickers) >= config.MAX_POSITIONS_PER_SECTOR else ""
        lines.append(f"  {sector}: {', '.join(tickers)} ({len(tickers)}){flag}")
    return "\n".join(lines)


# ---------- Equity curve ----------

def compute_equity_stats(
    closed_trades: list[dict],
    starting_capital: float,
    cash_movements: list[dict] | None = None,
) -> dict | None:
    """Equity curve + drawdown + per-trade Sharpe. Returns None if no closed trades.

    `cash_movements` (dividends etc.) are folded into the equity curve chronologically
    alongside trade exits so peak / max-drawdown reflect actual capital state. Sharpe
    is still computed only from trade pnl_pct (Sharpe is a trade-quality metric)."""
    if not closed_trades:
        return None

    events: list[tuple[str, float]] = [
        (t.get("exit_date") or "", float(t.get("pnl_eur") or 0))
        for t in closed_trades
    ]
    for m in (cash_movements or []):
        events.append((m.get("date") or "", float(m.get("amount") or 0)))
    events.sort(key=lambda e: e[0])

    equity = starting_capital
    peak = starting_capital
    max_dd_pct = 0.0
    max_dd_eur = 0.0
    for _date, delta in events:
        equity += delta
        if equity > peak:
            peak = equity
        dd_eur = peak - equity
        dd_pct = dd_eur / peak * 100 if peak > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_eur = dd_eur

    total_pnl_eur = round(equity - starting_capital, 2)
    total_return_pct = round((equity - starting_capital) / starting_capital * 100, 2) if starting_capital else 0.0

    # Per-trade Sharpe (mean/stdev of pnl_pct) — needs ≥2 trades.
    returns = [float(t.get("pnl_pct") or 0) for t in closed_trades]
    sharpe = None
    if len(returns) >= 2:
        import statistics
        avg = statistics.mean(returns)
        std = statistics.stdev(returns)
        if std > 0:
            sharpe = round(avg / std, 2)

    return {
        "starting_capital": round(starting_capital, 2),
        "current_equity": round(equity, 2),
        "total_pnl_eur": total_pnl_eur,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": round(max_dd_pct, 2),
        "max_drawdown_eur": round(max_dd_eur, 2),
        "sharpe_per_trade": sharpe,
        "trades_closed": len(closed_trades),
    }


def format_equity_stats(eq: dict) -> str:
    parts = [
        f"Equity: €{eq['current_equity']:.2f} (Start €{eq['starting_capital']:.2f})",
        f"Total P&L: €{eq['total_pnl_eur']:+.2f} ({eq['total_return_pct']:+.2f}%)",
        f"Max DD: {eq['max_drawdown_pct']:.2f}% (€{eq['max_drawdown_eur']:.2f})",
    ]
    if eq.get("sharpe_per_trade") is not None:
        parts.append(f"Sharpe/Trade: {eq['sharpe_per_trade']}")
    return " | ".join(parts)
