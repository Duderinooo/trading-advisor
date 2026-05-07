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
_PAPER_PORTFOLIO_PATH = Path(__file__).resolve().parent.parent / "training_portfolio.json"

# Guards every load-modify-save sequence on portfolio.json.
portfolio_lock = threading.RLock()
# Separate lock + file for the paper/training portfolio. Real and paper never share state.
paper_lock = threading.RLock()


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


def load_paper_portfolio() -> dict:
    """Load training/paper portfolio. Returns fresh default if missing.

    Paper-Spur lernt ohne Ausführungs-Risiko: jeder rec_entry, der alle Gates passt,
    wird automatisch geöffnet (mit €1/Seite Fee), via SL/TP-Loop geschlossen.
    Strikt getrennt von Real-Portfolio (eigene Datei, eigener Lock) damit Paper-Stats
    nie in Real-Brier-Haircut fließen.
    """
    if _PAPER_PORTFOLIO_PATH.exists():
        with open(_PAPER_PORTFOLIO_PATH) as f:
            return json.load(f)
    return {
        "open_trades": [],
        "closed_trades": [],
        "cash_eur": config.BUDGET_EUR,
        "total_capital_eur": config.BUDGET_EUR,
        "started_at": datetime.now().strftime("%Y-%m-%d"),
        "paper": True,
    }


def save_paper_portfolio(portfolio: dict):
    """Atomic write of paper portfolio. Same crash-safety as save_portfolio."""
    portfolio["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    portfolio["paper"] = True
    fd, tmp_path = tempfile.mkstemp(
        prefix=".training_portfolio_", suffix=".json",
        dir=_PAPER_PORTFOLIO_PATH.parent,
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(portfolio, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _PAPER_PORTFOLIO_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def exit_suppressed_tickers(portfolio: dict) -> set[str]:
    """Tickers für die KEINE neuen Event-/Haiku-Calls erzeugt werden sollen.

    Spart Haiku-Kosten + verhindert Re-Trigger-Loops, wenn User Exit ignoriert.
    Quellen:
      (a) pending_recommendations mit kind=exit (User hat schon Reminder bekommen
          oder sieht den ersten gleich; weitere Events bringen nichts).
      (b) open_trades mit exit_dropped_at < EXIT_REC_COOLDOWN_MIN_AFTER_DROP min
          (Auto-Drop hat stattgefunden; Cooldown verhindert sofortige Re-Recs).
    """
    out: set[str] = set()
    for rec in portfolio.get("pending_recommendations", []) or []:
        if rec.get("kind") == "exit":
            t = (rec.get("ticker") or "").upper()
            if t:
                out.add(t)
    cooldown_min = config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP
    now = datetime.now()
    for tr in portfolio.get("open_trades", []) or []:
        ts = tr.get("exit_dropped_at")
        if not ts:
            continue
        try:
            drop_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
            if (now - drop_dt).total_seconds() / 60.0 < cooldown_min:
                t = (tr.get("ticker") or "").upper()
                if t:
                    out.add(t)
        except ValueError:
            continue
    return out


def build_trade_dict(rec: dict, filled_price: float, shares: float,
                     entry_snapshot: dict | None = None,
                     paper: bool = False) -> dict:
    """Source of truth for open_trade dict shape.

    Used by /confirm (real) and _auto_paper_open (paper) so beide Spuren identisches
    Schema haben. shares = float weil TR-Bruchstücke (rounded 4 decimals).
    """
    size_eur = round(filled_price * shares, 2)
    return {
        "ticker": rec.get("ticker"),
        "entry_price": filled_price,
        "shares": shares,
        "size_eur": size_eur,
        "stop_loss": rec.get("stop_loss"),
        "take_profit": rec.get("take_profit"),
        "conviction": rec.get("conviction"),
        "p_win": rec.get("p_win"),
        "thesis": rec.get("thesis", ""),
        "setup_type": rec.get("setup_type"),
        "top_fail_mode": rec.get("top_fail_mode"),
        "hold_days_min": rec.get("hold_days_min"),
        "hold_days_max": rec.get("hold_days_max"),
        "trailing_stop_pct": rec.get("trailing_stop_pct"),
        "regime_at_entry": rec.get("regime_at_entry"),
        "vix_at_entry": rec.get("vix_at_entry"),
        "entry_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "open",
        "entry_snapshot": entry_snapshot,
        # MAE/MFE seeded at entry; main.py heartbeat ratchets each tick.
        # 0.0 default would freeze MAE forever (price never < 0); seed at entry instead.
        "mae": round(filled_price, 4),
        "mfe": round(filled_price, 4),
        "paper": paper,
    }


def add_cash_movement(
    portfolio: dict,
    *,
    amount: float,
    kind: str,
    ticker: str,
    note: str = "",
) -> dict:
    """Append a cash flow (e.g. dividend) to the cash_movements ledger and adjust cash_eur.

    Caller must already hold portfolio_lock. Returns the appended entry. Does not save —
    the surrounding transaction is responsible for save_portfolio().
    """
    movement = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "amount": round(float(amount), 2),
        "kind": kind,
        "ticker": ticker,
        "note": note or "",
    }
    portfolio.setdefault("cash_movements", []).append(movement)
    portfolio["cash_eur"] = round(
        float(portfolio.get("cash_eur", 0) or 0) + movement["amount"], 2
    )
    return movement


# ---------- Position sizing ----------

def compute_kelly_mult(closed_trades: list[dict]) -> float:
    """Adaptive Kelly fraction from rolling Brier-Score.

    Better calibration → bigger Kelly fraction. Worse → shrink. Range [0.10, 0.50].
    Until N≥10 scored trades exist, fall back to config.KELLY_FRACTION.

    Why: Kelly assumes edge estimate is correct. If our p_win is poorly calibrated,
    we overbet on bad estimates. Brier=0 perfect, 0.25 random — scale linearly.
    """
    scored = [
        t for t in closed_trades[-30:]
        if isinstance(t.get("brier"), (int, float))
    ]
    if len(scored) < 10:
        return config.KELLY_FRACTION
    avg_brier = sum(t["brier"] for t in scored) / len(scored)
    # Brier 0 → 0.50, Brier 0.25 (random) → 0.10. Linear interp.
    mult = 0.50 - (avg_brier / 0.25) * 0.40
    return max(0.10, min(0.50, round(mult, 2)))


def compute_slippage_budget(closed_trades: list[dict]) -> float:
    """Adaptive entry-slippage cap. Tightens if rolling avg slippage runs hot.

    Returns max-allowed slippage % (replaces static MAX_ENTRY_SLIPPAGE_PERCENT).
    Why: spread/liquidity drifts over time. Static gate either too lax (lets bad
    fills through) or too strict (blocks ok fills). Adaptive gate self-tunes.
    """
    recent = [
        abs(float(t.get("slippage_pct") or 0))
        for t in closed_trades[-30:]
        if t.get("slippage_pct") is not None
    ]
    base = config.MAX_ENTRY_SLIPPAGE_PERCENT
    if len(recent) < 10:
        return base
    avg = sum(recent) / len(recent)
    # If avg ≤0.3%: keep base. If avg ≥1.0%: clamp to 1.0%. Linear scale between.
    if avg <= 0.3:
        return base
    if avg >= 1.0:
        return 1.0
    return round(base - (avg - 0.3) / 0.7 * (base - 1.0), 2)


def max_affordable_share_price_eur(portfolio: dict) -> float:
    """Höchster Aktienpreis bei dem ≥1 ganzes Stück innerhalb der Position-Cap passt.

    TR-Stop-Loss läuft nur auf ganze Stücke; Bruchstück-Position = SL-unmöglich =
    Verstoß gegen Full-Trust-SL-Invariant. Dynamisch aus total_capital_eur ×
    MAX_POSITION_SIZE_PERCENT/100 × WHOLE_SHARE_PRICE_BUFFER.
    """
    capital = float(portfolio.get("total_capital_eur", config.BUDGET_EUR) or config.BUDGET_EUR)
    cap = capital * config.MAX_POSITION_SIZE_PERCENT / 100
    return cap * config.WHOLE_SHARE_PRICE_BUFFER


def suggest_position_size(
    atr14_pct: float | None,
    capital_eur: float,
    risk_pct: float = None,
    p_win: float | None = None,
    reward_to_risk: float | None = None,
    kelly_mult: float | None = None,
) -> float:
    """Position size: min(ATR-risk size, fractional-Kelly size, hard cap).

    ATR leg: capital × risk_pct / (1.5 × ATR%).
    Kelly leg (only if p_win + reward_to_risk given): f* = (p·b − (1−p))/b, scaled by kelly_mult.
    Hard cap: MAX_POSITION_SIZE_PERCENT of capital.

    `kelly_mult` defaults to config.KELLY_FRACTION but can be overridden with adaptive
    multiplier from `compute_kelly_mult(closed_trades)`.
    """
    if risk_pct is None:
        risk_pct = config.MAX_RISK_PER_TRADE_PERCENT
    if kelly_mult is None:
        kelly_mult = config.KELLY_FRACTION
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
            kelly_eur = capital_eur * f_kelly * kelly_mult
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

def compute_confluence(snap: dict, regime: str) -> dict:
    """Deterministic 0-10 confluence score for a LONG entry.
    Each item = 1 point. Aggregated score replaces gut-feel conviction.
    Returns {'score': int, 'items': dict[name, bool], 'missing': list[str]}.
    """
    if not isinstance(snap, dict) or snap.get("error") or not snap.get("price"):
        return {"score": 0, "items": {}, "missing": ["no_data"]}

    price = snap.get("price")
    ma20 = snap.get("ma20")
    ma50 = snap.get("ma50")
    rsi = snap.get("rsi14")
    macd = snap.get("macd")
    macd_sig = snap.get("macd_signal")
    vol_ratio = snap.get("volume_ratio")
    spread = snap.get("spread_pct")
    wk_trend = snap.get("wk_trend")
    rs = snap.get("rs_20d_vs_index_pct")
    rec_key = snap.get("analyst_rec_key")
    upside = snap.get("analyst_upside_pct")

    items: dict[str, bool] = {}
    items["wk_trend_up"] = (wk_trend == "UP")
    items["price_gt_ma50"] = bool(price and ma50 and price > ma50)
    items["price_gt_ma20"] = bool(price and ma20 and price > ma20)
    items["rsi_healthy"] = isinstance(rsi, (int, float)) and 40 <= rsi <= 70
    items["macd_bullish"] = isinstance(macd, (int, float)) and isinstance(macd_sig, (int, float)) and macd > macd_sig
    items["volume_ok"] = isinstance(vol_ratio, (int, float)) and vol_ratio >= 1.0
    items["spread_tight"] = isinstance(spread, (int, float)) and spread <= config.MAX_SPREAD_PERCENT / 2
    items["rs_positive"] = isinstance(rs, (int, float)) and rs >= 0
    items["analyst_bullish"] = (rec_key in ("strong_buy", "buy")) or (
        isinstance(upside, (int, float)) and upside >= 5
    )
    items["regime_risk_on"] = regime.startswith("RISK_ON") if isinstance(regime, str) else False

    score = sum(1 for v in items.values() if v)
    missing = [k for k, v in items.items() if not v]
    return {"score": score, "items": items, "missing": missing}


def format_confluence(c: dict) -> str:
    if not c or "score" not in c:
        return ""
    score = c["score"]
    items = c.get("items") or {}
    hit = [k for k, v in items.items() if v]
    miss = [k for k, v in items.items() if not v]
    return (
        f"Confluence {score}/10 — ✓ "
        + (", ".join(hit) if hit else "—")
        + (f" | ✗ {', '.join(miss)}" if miss else "")
    )


# ---------- Correlation gate ----------

def compute_correlations(returns_by_ticker: dict, candidate: str) -> dict[str, float]:
    """Pairwise correlation of `candidate` daily returns vs each other ticker.
    `returns_by_ticker` is dict[ticker -> pandas.Series of daily pct_change()].
    Returns dict[other_ticker -> corr_float]. Missing/short series omitted.
    """
    cand = returns_by_ticker.get(candidate)
    if cand is None or len(cand.dropna()) < 20:
        return {}
    out = {}
    for t, series in returns_by_ticker.items():
        if t == candidate or series is None or len(series.dropna()) < 20:
            continue
        try:
            c = float(cand.corr(series))
            if c == c:  # not NaN
                out[t] = round(c, 2)
        except Exception:
            continue
    return out


# ---------- Hit-rate stats ----------

def compute_hit_stats(closed_trades: list[dict]) -> dict | None:
    """Aggregate win-rate + R-multiple + conviction breakdown from closed trades.
    Returns None if not enough data (<3 closed trades)."""
    if not closed_trades or len(closed_trades) < 3:
        return None

    # Exact-zero pnl is break-even, semantically neither win nor loss — exclude
    # from both buckets so avg_loss_pct + r_multiple aren't dragged toward 0.
    wins = [t for t in closed_trades if (t.get("pnl_pct") or 0) > 0]
    losses = [t for t in closed_trades if (t.get("pnl_pct") or 0) < 0]
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

    # --- Mistake-class distribution + actionable suggestion (last 20 losses) ---
    recent_losses = [t for t in closed_trades if (t.get("pnl_pct") or 0) < 0][-20:]
    mistake_classes: dict[str, int] = {}
    for t in recent_losses:
        cls = t.get("mistake_class") or "untagged"
        mistake_classes[cls] = mistake_classes.get(cls, 0) + 1

    class_suggestion: str | None = None
    if recent_losses and len(recent_losses) >= 5:
        n_losses = len(recent_losses)
        # Ignore 'untagged' for dominance check (user may not have tagged yet).
        tagged = {c: n for c, n in mistake_classes.items() if c != "untagged"}
        if tagged:
            top_cls, top_n = max(tagged.items(), key=lambda x: x[1])
            share = top_n / n_losses
            if share >= 0.40:
                if top_cls == "execution":
                    class_suggestion = (
                        f"{top_cls} dominiert ({top_n}/{n_losses}={share:.0%}): "
                        f"MAX_SPREAD_PERCENT halbieren, Slippage-Limit strenger, "
                        f"keine Entries bei volume_ratio<0.8."
                    )
                elif top_cls == "timing":
                    class_suggestion = (
                        f"{top_cls} dominiert ({top_n}/{n_losses}={share:.0%}): "
                        f"Entry-Trigger strenger (Bestätigung auf 15m-Close statt Intraday), "
                        f"Breakout-Distance >1.0% statt 0.5%."
                    )
                elif top_cls == "prediction":
                    class_suggestion = (
                        f"{top_cls} dominiert ({top_n}/{n_losses}={share:.0%}): "
                        f"Conviction-Gate erhöhen (min 4/5 statt 3/5), "
                        f"p_win-Schwelle +0.05 (These muss stärker sein)."
                    )
                elif top_cls == "external":
                    class_suggestion = (
                        f"{top_cls} dominiert ({top_n}/{n_losses}={share:.0%}): "
                        f"Event-Kalender strikter (keine Entries 48h vor High-Impact), "
                        f"Position Size halbieren bei VIX>20."
                    )

    # --- Per-setup-type breakdown (which entry patterns work) ---
    by_setup: dict[str, dict] = {}
    for t in closed_trades:
        st = t.get("setup_type") or "untagged"
        by_setup.setdefault(st, {"wins": 0, "total": 0, "pnl_pct_sum": 0.0})
        by_setup[st]["total"] += 1
        if (t.get("pnl_pct") or 0) > 0:
            by_setup[st]["wins"] += 1
        by_setup[st]["pnl_pct_sum"] += (t.get("pnl_pct") or 0)
    for st, s in by_setup.items():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0
        s["avg_pnl_pct"] = round(s["pnl_pct_sum"] / s["total"], 2) if s["total"] else 0
        del s["pnl_pct_sum"]

    # --- Time-of-day / day-of-week bias on entry timestamp ---
    by_dow: dict[str, dict] = {}
    by_hour_bucket: dict[str, dict] = {}
    for t in closed_trades:
        ed = t.get("entry_date") or ""
        try:
            dt = datetime.strptime(ed, "%Y-%m-%d %H:%M")
        except Exception:
            continue
        dow = dt.strftime("%a")
        by_dow.setdefault(dow, {"wins": 0, "total": 0})
        by_dow[dow]["total"] += 1
        if (t.get("pnl_pct") or 0) > 0:
            by_dow[dow]["wins"] += 1

        # Hour buckets: open(09-10), morning(10-12), midday(12-15), us_open(15-17), late(17-22).
        h = dt.hour
        if 9 <= h < 10:
            bucket = "open"
        elif 10 <= h < 12:
            bucket = "morning"
        elif 12 <= h < 15:
            bucket = "midday"
        elif 15 <= h < 17:
            bucket = "us_open"
        else:
            bucket = "late"
        by_hour_bucket.setdefault(bucket, {"wins": 0, "total": 0})
        by_hour_bucket[bucket]["total"] += 1
        if (t.get("pnl_pct") or 0) > 0:
            by_hour_bucket[bucket]["wins"] += 1

    for bucket_dict in (by_dow, by_hour_bucket):
        for k, s in bucket_dict.items():
            s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0

    # --- Hold-duration buckets (entry → exit days) ---
    by_hold: dict[str, dict] = {}
    for t in closed_trades:
        ed = t.get("entry_date") or ""
        xd = t.get("exit_date") or ""
        try:
            d_in = datetime.strptime(ed, "%Y-%m-%d %H:%M")
            d_out = datetime.strptime(xd, "%Y-%m-%d %H:%M")
            held_days = max(0, (d_out - d_in).days)
        except Exception:
            continue
        if held_days <= 1:
            bucket = "0-1d"
        elif held_days <= 3:
            bucket = "2-3d"
        elif held_days <= 7:
            bucket = "4-7d"
        else:
            bucket = "8d+"
        by_hold.setdefault(bucket, {"wins": 0, "total": 0})
        by_hold[bucket]["total"] += 1
        if (t.get("pnl_pct") or 0) > 0:
            by_hold[bucket]["wins"] += 1
    for k, s in by_hold.items():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0

    # --- Regime × setup_type hit-rate (institutional: regime-conditional models) ---
    by_setup_regime: dict[str, dict] = {}
    for t in closed_trades:
        st = t.get("setup_type") or "untagged"
        rg = t.get("regime_at_entry") or "UNKNOWN"
        key = f"{st}@{rg}"
        by_setup_regime.setdefault(key, {"wins": 0, "total": 0})
        by_setup_regime[key]["total"] += 1
        if (t.get("pnl_pct") or 0) > 0:
            by_setup_regime[key]["wins"] += 1
    for k, s in by_setup_regime.items():
        s["rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0

    # --- Alpha vs Beta attribution (was loss skill or market noise?) ---
    attributed = [
        t for t in closed_trades
        if isinstance(t.get("alpha_pct"), (int, float))
    ]
    attribution: dict | None = None
    if len(attributed) >= 5:
        wins_alpha_pos = [t for t in attributed if (t.get("pnl_pct") or 0) > 0 and t["alpha_pct"] > 0]
        wins_alpha_neg = [t for t in attributed if (t.get("pnl_pct") or 0) > 0 and t["alpha_pct"] <= 0]
        loss_alpha_pos = [t for t in attributed if (t.get("pnl_pct") or 0) < 0 and t["alpha_pct"] > 0]
        loss_alpha_neg = [t for t in attributed if (t.get("pnl_pct") or 0) < 0 and t["alpha_pct"] <= 0]
        avg_alpha = sum(t["alpha_pct"] for t in attributed) / len(attributed)
        attribution = {
            "n": len(attributed),
            "avg_alpha_pct": round(avg_alpha, 2),
            "wins_with_alpha": len(wins_alpha_pos),       # real skill wins
            "wins_riding_market": len(wins_alpha_neg),    # lucky beta wins
            "losses_market_noise": len(loss_alpha_pos),   # lost despite beating market
            "losses_setup_fail": len(loss_alpha_neg),     # lost AND underperformed market
        }

    # --- Slippage tracking (drives adaptive gate) ---
    slip_recent = [
        abs(float(t.get("slippage_pct") or 0))
        for t in closed_trades[-30:]
        if t.get("slippage_pct") is not None
    ]
    slippage_stats: dict | None = None
    if len(slip_recent) >= 5:
        slippage_stats = {
            "n": len(slip_recent),
            "avg_pct": round(sum(slip_recent) / len(slip_recent), 3),
            "max_pct": round(max(slip_recent), 3),
            "current_budget_pct": compute_slippage_budget(closed_trades),
        }

    # --- Pre-mortem accuracy (did predicted top_fail_mode match reality?) ---
    fail_mode_to_class = {
        "support_breakdown": "prediction",
        "thesis_invalidation": "prediction",
        "earnings_miss": "external",
        "macro_event": "external",
        "regime_shift": "external",
        "sector_rotation": "external",
        "false_breakout": "timing",
        "stop_run": "timing",
    }
    premortem_losses = [
        t for t in closed_trades
        if (t.get("pnl_pct") or 0) < 0
        and t.get("top_fail_mode")
        and t.get("mistake_class")
    ]
    premortem_stats: dict | None = None
    if len(premortem_losses) >= 5:
        correct = sum(
            1 for t in premortem_losses
            if fail_mode_to_class.get(t["top_fail_mode"]) == t["mistake_class"]
        )
        premortem_stats = {
            "n": len(premortem_losses),
            "accuracy_pct": round(correct / len(premortem_losses) * 100, 1),
        }

    return {
        "total": total,
        "win_rate": round(len(wins) / total * 100, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "r_multiple": round(r_multiple, 2) if r_multiple else None,
        "total_pnl_eur": total_pnl_eur,
        "by_conviction": conv_stats,
        "by_setup": by_setup,
        "by_setup_regime": by_setup_regime,
        "by_dow": by_dow,
        "by_hour": by_hour_bucket,
        "by_hold": by_hold,
        "recent_streak": streak,
        "calibration": calibration,
        "mistake_classes": mistake_classes,
        "class_suggestion": class_suggestion,
        "attribution": attribution,
        "slippage_stats": slippage_stats,
        "premortem_stats": premortem_stats,
        "kelly_mult": compute_kelly_mult(closed_trades),
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
            # Haircut applied as `p_adj = p_raw - haircut`. Negative haircut →
            # under-confident → adjustment ADDS to p (be more aggressive).
            # Positive haircut → over-confident → adjustment SUBTRACTS (be stricter).
            hc = cal["haircut"]
            if hc > 0:
                action = f"sei STRENGER (Ziehe {hc:.2f} von neuen p_win ab — Bot war zu optimistisch)"
            else:
                action = f"sei AGGRESSIVER (Addiere {abs(hc):.2f} zu neuen p_win — Bot war zu pessimistisch)"
            line += (
                f" | KORREKTUR (auto-applied im edge gate, cap ±0.20): {action}"
            )
        lines.append(line)
    if stats.get("class_suggestion"):
        lines.append(f"🎯 SELBST-KALIBRIERUNG: {stats['class_suggestion']}")

    # Per-setup table (only show setups with ≥3 trades to avoid noise)
    setups = stats.get("by_setup") or {}
    setup_line = " | ".join(
        f"{name}: {s['rate']}% ({s['wins']}/{s['total']}, Ø{s['avg_pnl_pct']:+.1f}%)"
        for name, s in sorted(setups.items(), key=lambda x: -x[1]["total"])
        if s["total"] >= 3
    )
    if setup_line:
        lines.append(f"Setups: {setup_line}")

    # Day-of-week (only if at least one bucket ≥3)
    dows = stats.get("by_dow") or {}
    dow_line = " | ".join(
        f"{d}: {s['rate']}% ({s['wins']}/{s['total']})"
        for d, s in dows.items() if s["total"] >= 3
    )
    if dow_line:
        lines.append(f"DoW: {dow_line}")

    # Hour bucket (only if at least one bucket ≥3)
    hours = stats.get("by_hour") or {}
    hour_line = " | ".join(
        f"{h}: {s['rate']}% ({s['wins']}/{s['total']})"
        for h, s in hours.items() if s["total"] >= 3
    )
    if hour_line:
        lines.append(f"Entry-Zeit: {hour_line}")

    # Hold-duration
    holds = stats.get("by_hold") or {}
    hold_line = " | ".join(
        f"{b}: {s['rate']}% ({s['wins']}/{s['total']})"
        for b, s in sorted(holds.items()) if s["total"] >= 3
    )
    if hold_line:
        lines.append(f"Hold: {hold_line}")

    # Regime-conditional setups (only show buckets ≥3 — avoid noise from rare combos)
    sr = stats.get("by_setup_regime") or {}
    sr_line = " | ".join(
        f"{k}: {s['rate']}% ({s['wins']}/{s['total']})"
        for k, s in sorted(sr.items(), key=lambda x: -x[1]["total"])
        if s["total"] >= 3
    )
    if sr_line:
        lines.append(f"Setup×Regime: {sr_line}")

    # Alpha vs Beta attribution
    attr = stats.get("attribution")
    if attr:
        lines.append(
            f"Attribution (n={attr['n']}, Ø α {attr['avg_alpha_pct']:+.2f}%): "
            f"Wins skill {attr['wins_with_alpha']} / luck {attr['wins_riding_market']} | "
            f"Losses noise {attr['losses_market_noise']} / setup-fail {attr['losses_setup_fail']}"
        )

    # Slippage budget (adaptive)
    slip = stats.get("slippage_stats")
    if slip:
        lines.append(
            f"Slippage (last {slip['n']}): Ø {slip['avg_pct']}% | max {slip['max_pct']}% | "
            f"Budget aktuell: {slip['current_budget_pct']}%"
        )

    # Pre-mortem accuracy
    pm = stats.get("premortem_stats")
    if pm:
        lines.append(
            f"Pre-Mortem-Accuracy (last {pm['n']} losses): {pm['accuracy_pct']}% — "
            "predicted top_fail_mode entsprach realer mistake_class."
        )

    # Adaptive Kelly (only show if non-default)
    km = stats.get("kelly_mult")
    if isinstance(km, (int, float)) and km != config.KELLY_FRACTION:
        lines.append(f"Kelly-Mult: {km} (adaptiv aus Brier-Score)")

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
