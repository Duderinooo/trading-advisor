"""Multi-day SL/TP replay: walk a date range, applying replay_sltp_step each
weekday. Sequential — Day N's portfolio = Day N-1's mutated output. Useful for
self-audit of position-trajectory over the past week / month.

CLI:
    ./venv/bin/python -m core.replay.replay_range \\
        --start 2026-05-12 --end 2026-05-23
    ./venv/bin/python -m core.replay.replay_range \\
        --start 2026-05-01 --end 2026-05-23 \\
        --portfolio path/to/snapshot.json --json
"""

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from core.replay.replay_day import DayReplayResult, replay_sltp_step


logger = logging.getLogger(__name__)


@dataclass
class RangeReplayResult:
    """Aggregate outcome of replay_range over a date range."""
    start_date: str
    end_date: str
    days: list[DayReplayResult] = field(default_factory=list)
    cumulative_pnl_eur: float = 0.0
    total_closed_full: int = 0
    total_closed_partial: int = 0
    total_ambiguous: int = 0
    total_events: int = 0
    starting_open_count: int = 0
    ending_open_count: int = 0


def _is_trading_day(d: date) -> bool:
    """Mirror of runtime.scheduler.is_trading_day to avoid loading runtime here."""
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in config.XETRA_HOLIDAYS


def replay_range(
    start_date: date, end_date: date, portfolio: dict,
) -> tuple[dict, RangeReplayResult]:
    """Walk weekdays in [start_date, end_date] sequentially. Each day's mutated
    portfolio feeds the next day's replay.

    Returns (final_portfolio, RangeReplayResult). Input portfolio is deep-copied;
    caller's dict is never mutated."""
    if start_date > end_date:
        raise ValueError(f"start_date {start_date} > end_date {end_date}")

    pf = deepcopy(portfolio)
    result = RangeReplayResult(
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        starting_open_count=len(pf.get("open_trades", []) or []),
    )

    current = start_date
    while current <= end_date:
        if not _is_trading_day(current):
            current += timedelta(days=1)
            continue
        pf, day_result = replay_sltp_step(pf, current)
        result.days.append(day_result)
        result.cumulative_pnl_eur += day_result.realized_pnl_eur
        result.total_closed_full += day_result.closed_full
        result.total_closed_partial += day_result.closed_partial
        result.total_ambiguous += day_result.ambiguous
        result.total_events += len(day_result.events)
        current += timedelta(days=1)

    result.cumulative_pnl_eur = round(result.cumulative_pnl_eur, 2)
    result.ending_open_count = len(pf.get("open_trades", []) or [])
    return pf, result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Replay SL/TP loop across a date range using cached daily bars.",
    )
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument(
        "--portfolio", default=None,
        help="Path to portfolio JSON snapshot. Default: live portfolio.json",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if args.portfolio:
        portfolio = json.loads(Path(args.portfolio).read_text())
    else:
        from core.portfolio import load_portfolio
        portfolio = load_portfolio()

    final_pf, result = replay_range(start, end, portfolio)

    if args.json:
        out = {
            "start_date": result.start_date,
            "end_date": result.end_date,
            "cumulative_pnl_eur": result.cumulative_pnl_eur,
            "total_closed_full": result.total_closed_full,
            "total_closed_partial": result.total_closed_partial,
            "total_ambiguous": result.total_ambiguous,
            "total_events": result.total_events,
            "starting_open": result.starting_open_count,
            "ending_open": result.ending_open_count,
            "days": [
                {**d.__dict__, "events": d.events}
                for d in result.days
            ],
        }
        print(json.dumps(out, indent=2))
        return

    # Human-readable summary
    print(f"\nRange Replay: {result.start_date} → {result.end_date}")
    print(f"  Trading days replayed: {len(result.days)}")
    print(f"  Starting open: {result.starting_open_count}")
    print(f"  Ending open:   {result.ending_open_count}")
    print(f"  Cumulative P&L: €{result.cumulative_pnl_eur:+.2f}")
    print(f"  Full closes:    {result.total_closed_full}")
    print(f"  Partials:       {result.total_closed_partial}")
    print(f"  Ambiguous bars: {result.total_ambiguous}")
    print(f"  Total events:   {result.total_events}")
    days_with_events = [d for d in result.days if d.events]
    if days_with_events:
        print("\n  Daily breakdown (only days with events):")
        for d in days_with_events:
            sign = "+" if d.realized_pnl_eur >= 0 else ""
            print(f"    {d.date}: €{sign}{d.realized_pnl_eur:.2f}  "
                  f"(full={d.closed_full} partial={d.closed_partial} "
                  f"ambiguous={d.ambiguous})")


if __name__ == "__main__":
    _main()
