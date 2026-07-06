"""Portfolio JSON I/O + locks. Atomic writes via tmp + rename.

All load-modify-save paths MUST acquire `portfolio_lock` to prevent the
Telegram listener thread from clobbering writes by the main loop (and vice versa).
"""

import os
import json
import tempfile
import threading
import logging
from datetime import datetime, timedelta
from pathlib import Path

import config


logger = logging.getLogger(__name__)

_PORTFOLIO_PATH = Path(__file__).resolve().parent.parent.parent / "portfolio.json"

# Guards every load-modify-save sequence on portfolio.json.
portfolio_lock = threading.RLock()


def _load_portfolio_raw() -> dict:
    """Low-level read of portfolio.json without splicing satellite stores.
    Used by satellite-store migration helpers to avoid recursion."""
    if _PORTFOLIO_PATH.exists():
        with open(_PORTFOLIO_PATH) as f:
            return json.load(f)
    return {
        "open_trades": [],
        "closed_trades": [],
        "cash_eur": config.BUDGET_EUR,
        "total_capital_eur": config.BUDGET_EUR,
    }


def load_portfolio() -> dict:
    """Load current trading portfolio. Splices pending_recommendations
    (Phase E5) + closed_trades (Phase E4) from satellite state-stores so
    existing call-sites still see them under the familiar keys."""
    pf = _load_portfolio_raw()
    try:
        from core.portfolio.pending_store import load_pending
        pf["pending_recommendations"] = load_pending()
    except Exception:
        logger.exception("pending splice into load_portfolio failed")
    try:
        from core.portfolio.closed_trades_store import load_closed_trades
        pf["closed_trades"] = load_closed_trades()
    except Exception:
        logger.exception("closed_trades splice into load_portfolio failed")
    return pf


def save_portfolio(portfolio: dict):
    """Atomic write via tmp + rename. Always safe under crash.

    Extracts pending_recommendations + writes to state/pending.json before
    writing portfolio.json (transparent shim for Phase E5)."""
    portfolio["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Split out pending → state/pending.json (own lock)
    pending = portfolio.pop("pending_recommendations", None)
    if pending is not None:
        try:
            from core.portfolio.pending_store import save_pending
            save_pending(pending)
        except Exception:
            logger.exception("pending split-out at save_portfolio failed")

    # Split out closed_trades → state/closed_trades.jsonl (own lock).
    closed = portfolio.pop("closed_trades", None)
    if closed is not None:
        try:
            from core.portfolio.closed_trades_store import save_closed_trades
            save_closed_trades(closed)
        except Exception:
            logger.exception("closed_trades split-out at save_portfolio failed")

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


def downsample_equity_history(
    hist: list[dict], now: datetime | None = None, fine_days: int = 14,
) -> list[dict]:
    """Retain full 1-min resolution for the last `fine_days`; thin older points
    to one-per-hour (keep the first in each hour bucket). Preserves the long-run
    equity track record without unbounded growth.

    Replaces the hard `>fine_days` delete that silently ate history (2026-07-06:
    user saw the curve's left edge vanish daily). Shared by the heartbeat writer
    (main._collect_heartbeat) and the boot cleanup (runtime.scheduler) so the two
    retention paths can't drift. hist is chronological (append-order) → keeping
    the first point per hour bucket keeps the earliest sample deterministically."""
    if not hist:
        return hist
    now = now or datetime.now()
    cutoff = (now - timedelta(days=fine_days)).strftime("%Y-%m-%d %H:%M")
    recent = [p for p in hist if (p.get("ts") or "") >= cutoff]
    kept_old: list[dict] = []
    seen_hours: set[str] = set()
    for p in hist:
        ts = p.get("ts") or ""
        if ts >= cutoff:
            continue
        hour = ts[:13]  # "YYYY-MM-DD HH"
        if hour and hour not in seen_hours:
            seen_hours.add(hour)
            kept_old.append(p)
    return kept_old + recent


