"""Portfolio JSON I/O + locks. Atomic writes via tmp + rename.

All load-modify-save paths MUST acquire `portfolio_lock` to prevent the
Telegram listener thread from clobbering writes by the main loop (and vice versa).
"""

import os
import json
import tempfile
import threading
import logging
from pathlib import Path


logger = logging.getLogger(__name__)

_PORTFOLIO_PATH = Path(__file__).resolve().parent.parent.parent / "portfolio.json"
_PAPER_PORTFOLIO_PATH = Path(__file__).resolve().parent.parent.parent / "training_portfolio.json"

# Guards every load-modify-save sequence on portfolio.json.
portfolio_lock = threading.RLock()
# Separate lock + file for the paper/training portfolio. Real and paper never share state.
paper_lock = threading.RLock()


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
