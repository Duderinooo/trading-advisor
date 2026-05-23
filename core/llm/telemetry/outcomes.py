"""Blocked-trade outcome tracking (counterfactual).

When a gate blocks an entry, record the rec details. Next trading day, pull
the actual price action and compute whether the trade WOULD have won — i.e.
hit TP1 before SL. Surfaces per-gate `false_negative_rate` (blocked but
would-have-won) for gate-tuning decisions.

Storage:
- analytics/gate_outcomes_pending.jsonl — recently blocked, awaiting next-day data
- analytics/gate_outcomes.jsonl         — resolved outcomes

Resolution uses daily OHLC bars from yfinance. Ambiguous cases (low ≤ SL AND
high ≥ TP1 in the same day → can't tell order) are recorded as would_win=None.
A follow-up could use 15m bars for finer resolution.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf


logger = logging.getLogger(__name__)


_ANALYTICS_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "../../.." / "analytics"
_ANALYTICS_DIR = _ANALYTICS_DIR.resolve()
_PENDING_PATH = _ANALYTICS_DIR / "gate_outcomes_pending.jsonl"
_RESOLVED_PATH = _ANALYTICS_DIR / "gate_outcomes.jsonl"


# Gates where recording the counterfactual is meaningless: rec was malformed
# (no fields to evaluate), or measurement would be confounded.
_SKIP_GATES = {
    "gate_required_fields",   # rec incomplete by definition
    "gate_already_open",      # counterfactual = existing position outcome
    "gate_event_watch_coupling",  # rec invalid in event-mode without watch
}


def _ensure_dir() -> None:
    _ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)


def record_blocked_entry(
    *,
    gate_name: str,
    ticker: str,
    entry_price: float,
    stop_loss: float,
    take_profit: list | float | None,
    setup_type: str | None,
    regime: str,
) -> None:
    """Append a pending-outcome record. Caller filters which gates to skip
    via the _SKIP_GATES set (e.g. malformed-rec gates produce no measurable
    counterfactual)."""
    if gate_name in _SKIP_GATES:
        return
    if not (isinstance(entry_price, (int, float)) and isinstance(stop_loss, (int, float))
            and entry_price > stop_loss > 0):
        return
    tp_list = take_profit if isinstance(take_profit, list) else (
        [take_profit] if isinstance(take_profit, (int, float)) else []
    )
    if not tp_list:
        return
    _ensure_dir()
    record = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "date": str(date.today()),
        "ticker": ticker,
        "gate": gate_name,
        "setup_type": setup_type,
        "entry_price": float(entry_price),
        "stop_loss": float(stop_loss),
        "take_profit": [float(x) for x in tp_list],
        "regime": regime,
    }
    try:
        with _PENDING_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        logger.exception("Failed to append pending outcome for %s/%s", ticker, gate_name)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except Exception:
        logger.exception("Failed to read %s", path)
    return out


def _write_jsonl(path: Path, records: list[dict]) -> None:
    _ensure_dir()
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    tmp.replace(path)


def _resolve_against_daily_bar(record: dict, today: date) -> dict | None:
    """Resolve a pending record using the daily OHLC bar of the next trading day.

    Returns the enriched record (with would_win/peak_pct/trough_pct/etc) or
    None if no bar is available yet (too soon to resolve)."""
    rec_date_str = record.get("date") or ""
    try:
        rec_date = datetime.strptime(rec_date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
    if today <= rec_date:
        return None  # same-day, can't resolve yet
    ticker = record["ticker"]
    try:
        # Fetch a 5d window starting day-after-block (covers weekend gap to Monday).
        start = rec_date + timedelta(days=1)
        end = min(today + timedelta(days=1), start + timedelta(days=7))
        hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
    except Exception:
        logger.warning("yfinance fetch failed for %s outcome", ticker)
        return None
    if hist is None or hist.empty:
        return None
    # Take the FIRST available trading-day bar in the window.
    row = hist.iloc[0]
    high = float(row["High"])
    low = float(row["Low"])
    entry = float(record["entry_price"])
    sl = float(record["stop_loss"])
    tp1 = float(record["take_profit"][0])

    hit_sl = low <= sl
    hit_tp1 = high >= tp1
    if hit_sl and hit_tp1:
        would_win = None  # ambiguous: daily bar can't order intraday touches
    elif hit_tp1:
        would_win = True
    elif hit_sl:
        would_win = False
    else:
        would_win = None  # neither hit within 1d (no resolution)

    peak_pct = (high - entry) / entry * 100 if entry > 0 else 0.0
    trough_pct = (low - entry) / entry * 100 if entry > 0 else 0.0

    return {
        **record,
        "resolved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "resolved_bar_date": str(hist.index[0].date()),
        "next_day_high": high,
        "next_day_low": low,
        "would_hit_sl": hit_sl,
        "would_hit_tp1": hit_tp1,
        "would_win": would_win,
        "peak_pct": round(peak_pct, 2),
        "trough_pct": round(trough_pct, 2),
    }


def compute_pending_outcomes(today: date | None = None) -> dict:
    """Resolve all pending blocks whose date is < today. Moves them from the
    pending file to the resolved file. Returns summary stats."""
    if today is None:
        today = date.today()
    pending = _load_jsonl(_PENDING_PATH)
    if not pending:
        return {"resolved": 0, "remaining": 0, "false_neg_count": 0}

    resolved_records = _load_jsonl(_RESOLVED_PATH)
    still_pending: list[dict] = []
    newly_resolved = 0
    false_negs = 0  # blocked-but-would-have-won
    for rec in pending:
        enriched = _resolve_against_daily_bar(rec, today)
        if enriched is None:
            still_pending.append(rec)
            continue
        resolved_records.append(enriched)
        newly_resolved += 1
        if enriched["would_win"] is True:
            false_negs += 1

    _write_jsonl(_PENDING_PATH, still_pending)
    if newly_resolved > 0:
        _write_jsonl(_RESOLVED_PATH, resolved_records)

    return {
        "resolved": newly_resolved,
        "remaining": len(still_pending),
        "false_neg_count": false_negs,
    }


def gate_false_negative_rates() -> dict[str, dict]:
    """Per-gate: how often does a block correspond to a missed winner?
    Returns {gate_name: {total, would_win, false_negative_rate}}."""
    records = _load_jsonl(_RESOLVED_PATH)
    by_gate: dict[str, dict] = {}
    for r in records:
        g = r.get("gate") or "?"
        if g not in by_gate:
            by_gate[g] = {"total": 0, "would_win": 0, "ambiguous": 0}
        by_gate[g]["total"] += 1
        if r.get("would_win") is True:
            by_gate[g]["would_win"] += 1
        elif r.get("would_win") is None:
            by_gate[g]["ambiguous"] += 1
    for g, d in by_gate.items():
        d["false_negative_rate"] = (
            round(d["would_win"] / d["total"], 3) if d["total"] > 0 else 0.0
        )
    return by_gate
