"""Entry-gate + exit-rec cooldowns. Persisted on the trade or in the portfolio.

Both cooldowns prevent Claude from re-spending API calls on tickers that just
failed an intraday-stable gate or had an exit-rec auto-dropped after user ignore.
"""

import logging
from datetime import datetime

import config
from core.portfolio.io import load_portfolio, portfolio_lock, save_portfolio


logger = logging.getLogger(__name__)


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


def record_entry_gate_cooldown(ticker: str, gate: str, reason: str) -> None:
    """Persist an entry-gate cooldown after a RS/edge gate-block.

    RS-20d and edge are intraday-stable — a ticker that just failed one will
    fail it again next cycle. The cooldown lets the analyzer mark the ticker
    in the market_data dump so Claude skips re-recommending it. The hard gate
    still re-checks live data, so a genuine improvement is never missed.
    """
    t = (ticker or "").upper()
    if not t or t == "?":
        return
    # State now lives in state/runtime.json (not portfolio.json).
    from core.portfolio.runtime_store import load_runtime, save_runtime
    runtime = load_runtime()
    cds = runtime.setdefault("entry_gate_cooldowns", {})
    cds[t] = {
        "gate": gate,
        "reason": reason,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_runtime(runtime)


def active_entry_gate_cooldowns(portfolio: dict | None = None) -> dict[str, dict]:
    """Return {ticker: cooldown} for cooldowns younger than ENTRY_GATE_COOLDOWN_MIN.

    `portfolio` arg kept for backwards-compat but ignored — state reads from
    state/runtime.json. Expired entries pruned lazily on next save.
    """
    out: dict[str, dict] = {}
    from core.portfolio.runtime_store import load_runtime
    cds = load_runtime().get("entry_gate_cooldowns") or {}
    if not cds:
        return out
    now = datetime.now()
    for tkr, cd in cds.items():
        ts = cd.get("ts")
        if not ts:
            continue
        try:
            age_min = (now - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60.0
        except (ValueError, TypeError):
            continue
        if age_min < config.ENTRY_GATE_COOLDOWN_MIN:
            out[(tkr or "").upper()] = cd
    return out


STRATEGY_VERSION = "v6"  # Bump bei strukturellen Strategie-Änderungen für PnL-Attribution.
PROMPT_VERSION = "v6"    # Bump bei Prompt-Refactors. Erlaubt outcome-Vergleich per Version.
SNAPSHOT_SCHEMA_VERSION = "2026-05-22"  # Bump wenn market_data/entry_snapshot-Felder ändern.

# Trade-Lifecycle (für `status` field):
# - pending: in pending_recommendations, noch nicht /confirmed
# - open: in open_trades, kein TP gehittet
# - partial_exit: in open_trades, TP1 partial-fill aktiv (partial_seq ≥1)
# - closed: in closed_trades (SL/TP-Full/manual)
# - canceled: pending-rec verworfen (TTL/explicit-cancel)
