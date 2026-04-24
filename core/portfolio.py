"""Portfolio state: JSON I/O, thread-safe lock, risk + performance statistics.

All load-modify-save paths MUST acquire `portfolio_lock` to prevent the
Telegram listener thread from clobbering writes by the main loop (and vice versa).
"""

import os
import json
import tempfile
import threading
import logging
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_PORTFOLIO_PATH = Path(__file__).resolve().parent.parent / "portfolio.json"

# Guards every load-modify-save sequence on portfolio.json.
portfolio_lock = threading.RLock()


# ---------- I/O ----------

def load_portfolio() -> dict:
    """Load current trading portfolio from JSON file. Returns default shape if missing."""
    if _PORTFOLIO_PATH.exists():
        with open(_PORTFOLIO_PATH) as f:
            return json.load(f)
    return {
        "open_trades": [],
        "closed_trades": [],
        "cash_eur": config.BUDGET_EUR,
        "total_capital_eur": config.BUDGET_EUR,
    }


def save_portfolio(portfolio: dict):
    """Atomic write via tmp + rename. Always safe under crash."""
    portfolio["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    fd, tmp_path = tempfile.mkstemp(
        prefix=".portfolio_", suffix=".json", dir=_PORTFOLIO_PATH.parent
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(portfolio, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _PORTFOLIO_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ---------- Position sizing ----------

def suggest_position_size(
    atr14_pct: float | None,
    capital_eur: float,
    risk_pct: float = None,
    p_win: float | None = None,
    reward_to_risk: float | None = None,
) -> float:
    """Position size: min(ATR-risk size, fractional-Kelly size, hard cap).

    ATR leg: capital × risk_pct / (1.5 × ATR%).
    Kelly leg (only if p_win + reward_to_risk given): f* = (p·b − (1−p))/b, scaled by KELLY_FRACTION.
    Hard cap: MAX_POSITION_SIZE_PERCENT of capital.
    """
    if risk_pct is None:
        risk_pct = config.MAX_RISK_PER_TRADE_PERCENT
    max_eur = capital_eur * config.MAX_POSITION_SIZE_PERCENT / 100

    if not atr14_pct or atr14_pct <= 0:
        atr_size = capital_eur * 0.10
    else:
        stop_dist_pct = atr14_pct * 1.5
        atr_size = (capital_eur * risk_pct / 100) / (stop_dist_pct / 100)

    size = atr_size
    if isinstance(p_win, (int, float)) and 0 < p_win < 1 and \
       isinstance(reward_to_risk, (int, float)) and reward_to_risk > 0:
        b = reward_to_risk
        f_kelly = (p_win * b - (1 - p_win)) / b
        if f_kelly > 0:
            kelly_eur = capital_eur * f_kelly * config.KELLY_FRACTION
            size = min(size, kelly_eur)
        else:
            size = 0.0  # negative edge → no trade

    return round(min(size, max_eur), 2)


# ---------- Circuit breakers ----------

def _today_realized_pnl_eur(closed_trades: list[dict]) -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    return sum(
        float(t.get("pnl_eur") or 0)
        for t in closed_trades
        if (t.get("exit_date") or "").startswith(today)
    )


def kill_switch_active(portfolio: dict) -> bool:
    return bool(portfolio.get("kill_switch", False))


def set_kill_switch(on: bool, reason: str = "") -> dict:
    """Flip kill switch. Blocks new entries + event/news analyses. SL/TP monitoring continues."""
    with portfolio_lock:
        fresh = load_portfolio()
        fresh["kill_switch"] = bool(on)
        fresh["kill_switch_reason"] = reason if on else ""
        fresh["kill_switch_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M") if on else ""
        save_portfolio(fresh)
        return fresh


def risk_halt_status(portfolio: dict) -> dict:
    """Evaluate all halt conditions. Returns {'halt': bool, 'reasons': [...], 'metrics': {...}}.

    Halts:
      - kill switch (manual panic)
      - daily loss ≤ -DAILY_LOSS_HALT_PERCENT of total_capital_eur
      - current equity ≤ peak × (1 - DRAWDOWN_HALT_PERCENT/100), resume after +DRAWDOWN_RECOVERY_PERCENT
      - summed open heat ≥ MAX_PORTFOLIO_HEAT_PERCENT of capital
    """
    capital = float(portfolio.get("total_capital_eur", config.BUDGET_EUR) or config.BUDGET_EUR)
    closed = portfolio.get("closed_trades", [])
    reasons: list[str] = []

    if kill_switch_active(portfolio):
        ks_reason = portfolio.get("kill_switch_reason") or "manual"
        ks_ts = portfolio.get("kill_switch_ts") or ""
        reasons.append(f"KILL-SWITCH aktiv ({ks_reason}; seit {ks_ts})")

    daily_pnl = _today_realized_pnl_eur(closed)
    daily_pnl_pct = (daily_pnl / capital * 100) if capital > 0 else 0.0
    if daily_pnl_pct <= -config.DAILY_LOSS_HALT_PERCENT:
        reasons.append(
            f"Daily-Loss-Cap: {daily_pnl_pct:.2f}% ≤ -{config.DAILY_LOSS_HALT_PERCENT}%"
        )

    starting = capital - sum(float(t.get("pnl_eur") or 0) for t in closed)
    equity = starting
    peak = starting
    for t in sorted(closed, key=lambda x: x.get("exit_date") or ""):
        equity += float(t.get("pnl_eur") or 0)
        if equity > peak:
            peak = equity
    dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0.0
    if dd_pct >= config.DRAWDOWN_HALT_PERCENT:
        reasons.append(
            f"Drawdown-Halt: {dd_pct:.2f}% ≥ {config.DRAWDOWN_HALT_PERCENT}% vom Peak"
        )

    heat = compute_portfolio_heat(portfolio)
    if heat["heat_pct"] >= config.MAX_PORTFOLIO_HEAT_PERCENT:
        reasons.append(
            f"Portfolio-Heat: {heat['heat_pct']:.2f}% ≥ {config.MAX_PORTFOLIO_HEAT_PERCENT}%"
        )

    return {
        "halt": bool(reasons),
        "reasons": reasons,
        "metrics": {
            "daily_pnl_pct": round(daily_pnl_pct, 2),
            "drawdown_pct": round(dd_pct, 2),
            "heat_pct": heat["heat_pct"],
        },
    }


def edge_ok(p_win: float | None, entry: float, stop: float, take_profit) -> tuple[bool, float]:
    """Check positive-expectancy gate. Returns (ok, edge). edge = p·b − (1−p) where b = reward/risk."""
    if not isinstance(p_win, (int, float)) or not (0 < p_win < 1):
        return False, 0.0
    if not (entry and stop and entry > stop > 0):
        return False, 0.0
    tp = take_profit[0] if isinstance(take_profit, list) and take_profit else take_profit
    if not tp or tp <= entry:
        return False, 0.0
    b = (tp - entry) / (entry - stop)
    edge = p_win * b - (1 - p_win)
    return edge >= config.MIN_EXPECTED_EDGE, edge


# ---------- Hit-rate stats ----------

def compute_hit_stats(closed_trades: list[dict]) -> dict | None:
    """Aggregate win-rate + R-multiple + conviction breakdown from closed trades.
    Returns None if not enough data (<3 closed trades)."""
    if not closed_trades or len(closed_trades) < 3:
        return None

    wins = [t for t in closed_trades if (t.get("pnl_pct") or 0) > 0]
    losses = [t for t in closed_trades if (t.get("pnl_pct") or 0) <= 0]
    total = len(closed_trades)

    avg_win = sum((t.get("pnl_pct") or 0) for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum((t.get("pnl_pct") or 0) for t in losses) / len(losses) if losses else 0.0
    r_multiple = (avg_win / abs(avg_loss)) if avg_loss else None

    by_conv: dict[int, list] = {}
    for t in closed_trades:
        c = t.get("conviction")
        if isinstance(c, (int, float)):
            by_conv.setdefault(int(c), []).append(t)
    conv_stats = {
        c: {
            "wins": sum(1 for t in ts if (t.get("pnl_pct") or 0) > 0),
            "total": len(ts),
        }
        for c, ts in by_conv.items()
    }
    for c in conv_stats:
        s = conv_stats[c]
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0

    streak = "".join(
        "W" if (t.get("pnl_pct") or 0) > 0 else "L"
        for t in closed_trades[-5:]
    )

    total_pnl_eur = round(sum((t.get("pnl_eur") or 0) for t in closed_trades), 2)

    # --- Brier-Score + Kalibrierung (rolling 20) ---
    # Nur Trades mit p_win-Prediction zählen. Ältere Trades ohne p_win werden ignoriert.
    scored = [
        t for t in closed_trades[-20:]
        if isinstance(t.get("p_win"), (int, float))
        and isinstance(t.get("brier"), (int, float))
    ]
    calibration: dict | None = None
    if scored:
        n = len(scored)
        avg_brier = sum(t["brier"] for t in scored) / n
        avg_p_pred = sum(t["p_win"] for t in scored) / n
        actual_win_rate = sum(t.get("outcome", 0) for t in scored) / n
        bias = avg_p_pred - actual_win_rate  # >0 = overconfident
        # Suggested haircut: wenn overconfident >5%-Punkte, p_effective = p_raw - bias
        haircut = round(bias, 3) if abs(bias) >= 0.05 else 0.0
        calibration = {
            "n": n,
            "avg_brier": round(avg_brier, 4),
            "avg_p_predicted": round(avg_p_pred, 3),
            "actual_win_rate": round(actual_win_rate, 3),
            "bias": round(bias, 3),
            "haircut": haircut,
        }

    return {
        "total": total,
        "win_rate": round(len(wins) / total * 100, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "r_multiple": round(r_multiple, 2) if r_multiple else None,
        "total_pnl_eur": total_pnl_eur,
        "by_conviction": conv_stats,
        "recent_streak": streak,
        "calibration": calibration,
    }


def format_hit_stats(stats: dict) -> str:
    if not stats:
        return ""
    r_str = f" | R {stats['r_multiple']}" if stats.get("r_multiple") else ""
    conv_line = " | ".join(
        f"Conv {c}/5: {s['rate']}% ({s['wins']}/{s['total']})"
        for c, s in sorted(stats.get("by_conviction", {}).items(), reverse=True)
    )
    lines = [
        f"{stats['total']} Trades | Win-Rate {stats['win_rate']}% | "
        f"Ø Win +{stats['avg_win_pct']}% | Ø Loss {stats['avg_loss_pct']}%{r_str} | "
        f"Total P&L €{stats['total_pnl_eur']:+.2f}"
    ]
    if conv_line:
        lines.append(conv_line)
    if stats.get("recent_streak"):
        lines.append(f"Last 5: {' '.join(stats['recent_streak'])}")
    cal = stats.get("calibration")
    if cal:
        direction = "overconfident" if cal["bias"] > 0 else "underconfident"
        line = (
            f"Brier (last {cal['n']}): {cal['avg_brier']} | "
            f"p_pred Ø {cal['avg_p_predicted']} vs tatsächlich {cal['actual_win_rate']} "
            f"→ {direction} um {abs(cal['bias']):.2f}"
        )
        if cal["haircut"]:
            line += (
                f" | KORREKTUR: Ziehe {cal['haircut']:+.2f} von neuen p_win ab "
                f"(Bias >5% — sei strenger)"
            )
        lines.append(line)
    return "\n".join(lines)


# ---------- Portfolio heat (risk-sizing guardrail) ----------

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

def compute_equity_stats(closed_trades: list[dict], starting_capital: float) -> dict | None:
    """Equity curve + drawdown + per-trade Sharpe. Returns None if no closed trades."""
    if not closed_trades:
        return None

    ordered = sorted(closed_trades, key=lambda x: x.get("exit_date") or "")

    equity = starting_capital
    peak = starting_capital
    max_dd_pct = 0.0
    max_dd_eur = 0.0
    for t in ordered:
        pnl = float(t.get("pnl_eur") or 0)
        equity += pnl
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
    returns = [float(t.get("pnl_pct") or 0) for t in ordered]
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
        "trades_closed": len(ordered),
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
