"""Portfolio JSON I/O + locks. Atomic writes via tmp + rename.

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


