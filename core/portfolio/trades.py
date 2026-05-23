"""Trade dict construction + cash movements ledger.

build_trade_dict = source of truth for the open_trade shape (real + paper).
add_cash_movement = dividend / deposit / withdrawal ledger entry.

Version constants (STRATEGY_VERSION, PROMPT_VERSION, SNAPSHOT_SCHEMA_VERSION)
live here because build_trade_dict stamps them onto every trade for outcome
attribution per version.
"""

import logging
from datetime import datetime

import config


logger = logging.getLogger(__name__)


STRATEGY_VERSION = "v6"  # Bump bei strukturellen Strategie-Änderungen für PnL-Attribution.
PROMPT_VERSION = "v6"    # Bump bei Prompt-Refactors. Erlaubt outcome-Vergleich per Version.
SNAPSHOT_SCHEMA_VERSION = "2026-05-22"  # Bump wenn market_data/entry_snapshot-Felder ändern.

# Trade-Lifecycle (für `status` field):
# - pending: in pending_recommendations, noch nicht /confirmed
# - open: in open_trades, kein TP gehittet
# - partial_exit: in open_trades, TP1 partial-fill aktiv (partial_seq ≥1)
# - closed: in closed_trades (SL/TP-Full/manual)
# - canceled: pending-rec verworfen (TTL/explicit-cancel)


def build_trade_dict(rec: dict, filled_price: float, shares: float,
                     entry_snapshot: dict | None = None,
                     paper: bool = False) -> dict:
    """Source of truth for open_trade dict shape.

    Used by /confirm (real) and _auto_paper_open (paper) so beide Spuren identisches
    Schema haben. shares = float weil TR-Bruchstücke (rounded 4 decimals).
    """
    size_eur = round(filled_price * shares, 2)
    snap = entry_snapshot or {}
    base_quality_at_entry = snap.get("base_quality_score")
    confluence_at_entry = snap.get("confluence_score")

    # R-Multiple tracking: initial_risk_per_share = entry−SL (LONG-only).
    # realized_r wird beim Close berechnet, max_r_open vom SL/TP-Loop ratched.
    sl = rec.get("stop_loss")
    initial_risk_per_share = (
        round(filled_price - float(sl), 4)
        if isinstance(sl, (int, float)) and sl > 0 and filled_price > float(sl)
        else None
    )

    # Market-Session aus aktueller Zeit (XETRA 09-17:30 / US 15:30-22:00 CET).
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    if 9*60 <= hm < 15*60 + 30:
        session = "xetra"
    elif 15*60 + 30 <= hm < 22*60:
        session = "us_overlap"
    elif 22*60 <= hm or hm < 9*60:
        session = "after_hours"
    else:
        session = "unknown"

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
        # Decision-Context (LLM-Output, was hat Sonnet als Trigger gesehen).
        "entry_state": rec.get("entry_state"),
        "primary_signal": rec.get("primary_signal"),
        "why_now": rec.get("why_now"),
        # Regime-Context (Engine-derivable, sollte nie null sein).
        "regime_at_entry": rec.get("regime_at_entry") or snap.get("regime") or "UNKNOWN",
        "vix_at_entry": rec.get("vix_at_entry") if rec.get("vix_at_entry") is not None else snap.get("vix_price"),
        "spy_above_ma200": rec.get("spy_above_ma200"),
        # Quality-Scores at entry (already extracted top-level for cheap bucketing).
        "base_quality_at_entry": base_quality_at_entry,
        "confluence_at_entry": confluence_at_entry,
        # Execution-Context (Slippage gets filled by /confirm handler).
        "market_session_at_entry": session,
        "spread_pct_at_entry": snap.get("spread_pct"),
        "gap_at_open_pct_at_entry": (
            round((snap.get("day_open") - snap.get("prev_close")) / snap.get("prev_close") * 100, 3)
            if isinstance(snap.get("day_open"), (int, float))
            and isinstance(snap.get("prev_close"), (int, float))
            and snap.get("prev_close") > 0
            else None
        ),
        # R-Multiple Tracking (realized_r + max_r_open werden vom Bot fortgeschrieben).
        "initial_risk_per_share": initial_risk_per_share,
        "realized_r": None,
        "max_r_open": 0.0,
        # Provenance — welcher Claude + welche Prompt/Decision-Version hat den Rec gebaut.
        "model": rec.get("model"),
        "prompt_version": rec.get("prompt_version") or PROMPT_VERSION,
        "strategy_version": rec.get("strategy_version") or STRATEGY_VERSION,
        "decision_version": rec.get("decision_version") or PROMPT_VERSION,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        # Partial-TP-Tracking (ratched bei TP-Fills in events.py).
        "partial_close_count": 0,
        "total_partial_pnl_eur": 0.0,
        # Lifecycle
        "entry_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "open",
        "entry_snapshot": entry_snapshot,
        # MAE/MFE seeded at entry; main.py heartbeat ratchets each tick.
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
    ex_date: str | None = None,
) -> dict:
    """Append a cash flow (e.g. dividend) to the cash_movements ledger and adjust cash_eur.

    For dividends: auto-link to the trade that was OPEN on `ex_date` (default = today).
    If no open match, link to the most-recent closed trade on the same ticker so
    realized R-Multiple stats include the dividend payout instead of attributing it
    to "free cash" (Bug 2026-05-07: RWE.DE -€13.50 trade pnl + €7.20 div was shown
    as -€13.50 in hit_stats, distorting Brier + R-Multiple downward).

    `linked_trade` is a dict {ticker, entry_date, exit_date, status}. Identifies the
    target trade unambiguously (composite key, no fragile indices).

    Caller must already hold portfolio_lock. Returns the appended entry. Does not save —
    the surrounding transaction is responsible for save_portfolio().
    """
    today = datetime.now().strftime("%Y-%m-%d")
    movement = {
        "date": today,
        "amount": round(float(amount), 2),
        "kind": kind,
        "ticker": ticker,
        "note": note or "",
    }

    if kind == "dividend":
        ex = ex_date or today
        ticker_u = (ticker or "").upper()
        # Open trade on ex_date?
        for tr in portfolio.get("open_trades", []) or []:
            if (tr.get("ticker") or "").upper() != ticker_u:
                continue
            ed = (tr.get("entry_date") or "")[:10]
            if ed and ed <= ex:
                movement["linked_trade"] = {
                    "ticker": ticker_u,
                    "entry_date": tr.get("entry_date"),
                    "exit_date": None,
                    "status": "open",
                }
                break
        else:
            # No open match: link to closed trade where ex_date ∈ [entry, exit].
            best = None
            for tr in portfolio.get("closed_trades", []) or []:
                if (tr.get("ticker") or "").upper() != ticker_u:
                    continue
                ed = (tr.get("entry_date") or "")[:10]
                xd = (tr.get("exit_date") or "")[:10]
                if ed and xd and ed <= ex <= xd:
                    best = tr
                    break  # First in-range match wins.
            if best is None:
                # Fallback: most recent closed trade on ticker.
                candidates = [
                    tr for tr in portfolio.get("closed_trades", []) or []
                    if (tr.get("ticker") or "").upper() == ticker_u
                ]
                if candidates:
                    best = max(candidates, key=lambda t: t.get("exit_date") or "")
            if best is not None:
                movement["linked_trade"] = {
                    "ticker": ticker_u,
                    "entry_date": best.get("entry_date"),
                    "exit_date": best.get("exit_date"),
                    "status": best.get("status") or "closed",
                }

    portfolio.setdefault("cash_movements", []).append(movement)
    portfolio["cash_eur"] = round(
        float(portfolio.get("cash_eur", 0) or 0) + movement["amount"], 2
    )
    return movement


# ---------- Position sizing ----------
