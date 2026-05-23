"""Risk gates: kill-switch + drawdown latch + portfolio heat + daily loss cap + edge.

risk_halt_status: master gate consulted by analyzer.handle_entry — returns
{halt: bool, reasons: list, metrics: dict}. Halted = blocks new entries.
SL/TP loop runs unaffected.

maintain_drawdown_state: hysteresis-driven latch — set DD halt when equity hits
DRAWDOWN_HALT_PERCENT below peak; clear when equity gains DRAWDOWN_RECOVERY back.

edge_ok: positive-expectancy check used by gate_edge.
"""

import logging
from datetime import datetime

import config
from core.portfolio.io import load_portfolio, portfolio_lock, save_portfolio
from core.portfolio.heat import compute_portfolio_heat


logger = logging.getLogger(__name__)


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


def _equity_curve(portfolio: dict) -> tuple[float, float, float]:
    """Returns (equity_now, peak, dd_pct) using frozen starting capital + realized pnl + cash movements.
    `total_capital_eur` is treated as the original deposit (constant), not current equity."""
    starting = float(portfolio.get("total_capital_eur", config.BUDGET_EUR) or config.BUDGET_EUR)
    events: list[tuple[str, float]] = []
    for t in portfolio.get("closed_trades", []):
        events.append((t.get("exit_date") or "", float(t.get("pnl_eur") or 0)))
    for m in portfolio.get("cash_movements", []):
        events.append((m.get("date") or "", float(m.get("amount") or 0)))
    events.sort(key=lambda e: e[0])
    equity = starting
    peak = starting
    for _date, delta in events:
        equity += delta
        if equity > peak:
            peak = equity
    dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0.0
    return equity, peak, dd_pct


def maintain_drawdown_state(portfolio: dict) -> bool:
    """Drawdown hysteresis: latch halt on entry threshold, lift only on recovery threshold.
    Why: without hysteresis, equity flickering around the halt-line toggles state every call.
    Mutates portfolio in-place. Returns True if state changed."""
    equity, _peak, dd_pct = _equity_curve(portfolio)

    active = bool(portfolio.get("dd_halt_active"))
    trough = portfolio.get("dd_halt_trough_eur")
    changed = False

    if not active:
        if dd_pct >= config.DRAWDOWN_HALT_PERCENT:
            portfolio["dd_halt_active"] = True
            portfolio["dd_halt_trough_eur"] = round(equity, 2)
            portfolio["dd_halt_started_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            changed = True
    else:
        if not isinstance(trough, (int, float)) or equity < trough:
            portfolio["dd_halt_trough_eur"] = round(equity, 2)
            trough = equity
        recovery_target = trough * (1 + config.DRAWDOWN_RECOVERY_PERCENT / 100)
        if equity >= recovery_target:
            portfolio["dd_halt_active"] = False
            portfolio["dd_halt_trough_eur"] = None
            portfolio["dd_halt_started_date"] = None
            changed = True

    return changed


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

    _eq, _peak, dd_pct = _equity_curve(portfolio)
    # Hysteresis: prefer latched state if active, else fresh threshold check.
    if portfolio.get("dd_halt_active"):
        trough = portfolio.get("dd_halt_trough_eur")
        recovery = (
            f", Recovery-Ziel €{trough * (1 + config.DRAWDOWN_RECOVERY_PERCENT/100):.2f}"
            if isinstance(trough, (int, float)) else ""
        )
        reasons.append(
            f"Drawdown-Halt latched: {dd_pct:.2f}% (Trough €{trough}{recovery})"
        )
    elif dd_pct >= config.DRAWDOWN_HALT_PERCENT:
        reasons.append(
            f"Drawdown-Halt: {dd_pct:.2f}% ≥ {config.DRAWDOWN_HALT_PERCENT}% vom Peak"
        )

    heat = compute_portfolio_heat(portfolio)
    if heat["heat_pct"] >= config.MAX_PORTFOLIO_HEAT_PERCENT:
        reasons.append(
            f"Portfolio-Heat: {heat['heat_pct']:.2f}% ≥ {config.MAX_PORTFOLIO_HEAT_PERCENT}%"
        )

    open_count = len(portfolio.get("open_trades", []))
    if open_count >= config.MAX_ACTIVE_TRADES:
        reasons.append(
            f"Max-Active-Trades: {open_count}/{config.MAX_ACTIVE_TRADES} offen"
        )

    today = datetime.now().strftime("%Y-%m-%d")
    # Dedup by (ticker, entry_date): partial-TP keeps trade in open_trades AND
    # adds a 'closed_partial' entry to closed_trades with the same entry_date.
    # Without dedup, a single trade with 1 partial close eats 2/2 of the daily cap.
    seen_today: set[tuple[str, str]] = set()
    for t in portfolio.get("open_trades", []):
        ed = t.get("entry_date") or ""
        if ed.startswith(today):
            seen_today.add(((t.get("ticker") or "").upper(), ed))
    for t in closed:
        ed = t.get("entry_date") or ""
        if ed.startswith(today):
            seen_today.add(((t.get("ticker") or "").upper(), ed))
    trades_today = len(seen_today)
    if trades_today >= config.MAX_TRADES_PER_DAY:
        reasons.append(
            f"Max-Trades-Per-Day: {trades_today}/{config.MAX_TRADES_PER_DAY} heute"
        )

    return {
        "halt": bool(reasons),
        "reasons": reasons,
        "metrics": {
            "daily_pnl_pct": round(daily_pnl_pct, 2),
            "drawdown_pct": round(dd_pct, 2),
            "heat_pct": heat["heat_pct"],
            "open_count": open_count,
            "trades_today": trades_today,
        },
    }


def dd_scaling_factor(portfolio: dict) -> float:
    """Soft drawdown scaling: between SOFT and HALT thresholds, halve risk-per-trade.
    Hard halt handled separately via risk_halt_status. Returns 1.0 (full size) or 0.5 (halved)."""
    _eq, _peak, dd_pct = _equity_curve(portfolio)
    if dd_pct >= config.DRAWDOWN_SOFT_PERCENT and dd_pct < config.DRAWDOWN_HALT_PERCENT:
        return 0.5
    return 1.0


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


# ---------- Confluence scoring ----------
