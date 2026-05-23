"""Single-trade walkthrough: feed (ticker, entry, sl, tp, date) → simulate
the trade bar-by-bar against historical daily OHLC.

Returns a structured outcome:
- bars_walked, resolution_bar_date
- hit_sl, hit_tp1
- final_outcome: "win" | "loss" | "still_open" | "ambiguous"
- realized_r (R-multiple)
- peak_pct / trough_pct (MFE/MAE in percent of entry)
- mae / mfe (absolute prices)

Use cases:
- Standalone: `python -m core.replay.walkthrough --ticker BAS.DE --entry 53.0 \\
  --sl 52.0 --tp 55.0 --date 2026-05-12 --window 10`
- Programmatic: build a multi-trade sweep in Phase D Stage 3 (parameter
  comparison) without re-fetching yfinance each iteration.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from core.data.historical import get_daily_bars_range


logger = logging.getLogger(__name__)


@dataclass
class WalkOutcome:
    ticker: str
    entry_date: str
    entry_price: float
    stop_loss: float
    take_profit: float
    bars_walked: int
    resolution_bar_date: str | None
    hit_sl: bool
    hit_tp1: bool
    final_outcome: str            # "win" / "loss" / "still_open" / "ambiguous"
    realized_r: float | None      # R-multiple = (exit-entry)/(entry-sl), None if open
    peak_pct: float               # MFE — max high vs entry as %
    trough_pct: float             # MAE — min low vs entry as %
    mfe_price: float              # max price reached
    mae_price: float              # min price reached
    bar_path: list[dict] = field(default_factory=list)  # one entry per bar walked


def walk_trade(
    ticker: str,
    entry_date: date,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    *,
    window_days: int = 10,
) -> WalkOutcome | None:
    """Walk daily bars from day-after-entry for up to `window_days` trading
    days. Returns WalkOutcome with hit/r-multiple/MFE/MAE, or None if no bars
    are available (fresh-cached failure)."""
    if not (entry_price > stop_loss > 0 and take_profit > entry_price):
        raise ValueError(
            f"Invalid LONG params: entry={entry_price} sl={stop_loss} tp={take_profit}"
        )
    start = entry_date + timedelta(days=1)
    end = start + timedelta(days=int(window_days * 1.6) + 4)  # ~1.6 cal days per weekday
    bars = get_daily_bars_range(ticker, start, end)
    if not bars:
        return None
    bars = bars[:window_days]

    risk = entry_price - stop_loss
    peak_high = entry_price
    trough_low = entry_price
    bar_path: list[dict] = []
    hit_sl = False
    hit_tp1 = False
    resolution_bar = None
    for bar in bars:
        high = bar["high"]
        low = bar["low"]
        peak_high = max(peak_high, high)
        trough_low = min(trough_low, low)
        bar_path.append({
            "date": bar["date"],
            "high": high, "low": low, "close": bar["close"],
            "tp_touch": high >= take_profit,
            "sl_touch": low <= stop_loss,
        })
        if low <= stop_loss or high >= take_profit:
            hit_sl = low <= stop_loss
            hit_tp1 = high >= take_profit
            resolution_bar = bar
            break

    if resolution_bar is None:
        final_outcome = "still_open"
        realized_r = None
    elif hit_sl and hit_tp1:
        final_outcome = "ambiguous"
        realized_r = None  # daily-bar order ambiguity
    elif hit_tp1:
        final_outcome = "win"
        realized_r = (take_profit - entry_price) / risk if risk > 0 else None
    else:
        final_outcome = "loss"
        realized_r = (stop_loss - entry_price) / risk if risk > 0 else None

    return WalkOutcome(
        ticker=ticker,
        entry_date=entry_date.isoformat(),
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        bars_walked=len(bar_path),
        resolution_bar_date=resolution_bar["date"] if resolution_bar else None,
        hit_sl=hit_sl,
        hit_tp1=hit_tp1,
        final_outcome=final_outcome,
        realized_r=round(realized_r, 3) if isinstance(realized_r, float) else None,
        peak_pct=round((peak_high - entry_price) / entry_price * 100, 2),
        trough_pct=round((trough_low - entry_price) / entry_price * 100, 2),
        mfe_price=round(peak_high, 4),
        mae_price=round(trough_low, 4),
        bar_path=bar_path,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Walk a single trade bar-by-bar.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--entry", type=float, required=True)
    parser.add_argument("--sl", type=float, required=True)
    parser.add_argument("--tp", type=float, required=True)
    parser.add_argument("--date", required=True, help="entry date YYYY-MM-DD")
    parser.add_argument("--window", type=int, default=10, help="max trading days (default 10)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    entry_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    outcome = walk_trade(
        args.ticker, entry_date, args.entry, args.sl, args.tp,
        window_days=args.window,
    )
    if outcome is None:
        print(f"No bars available for {args.ticker} from {entry_date}.")
        return

    if args.json:
        print(json.dumps({
            **{k: v for k, v in outcome.__dict__.items() if k != "bar_path"},
            "bar_path": outcome.bar_path,
        }, indent=2))
        return

    # Human-readable output
    print(f"\nWalkthrough: {outcome.ticker} from {outcome.entry_date}")
    print(f"  Entry: €{outcome.entry_price:.2f}  SL: €{outcome.stop_loss:.2f}  TP: €{outcome.take_profit:.2f}")
    print(f"  Outcome: {outcome.final_outcome.upper()}", end="")
    if outcome.realized_r is not None:
        print(f"  (realized R = {outcome.realized_r:+.2f})")
    else:
        print()
    print(f"  Bars walked: {outcome.bars_walked}, resolved on: {outcome.resolution_bar_date}")
    print(f"  MFE: +{outcome.peak_pct:.2f}% (€{outcome.mfe_price:.2f})  "
          f"MAE: {outcome.trough_pct:+.2f}% (€{outcome.mae_price:.2f})")
    print("\n  Bar trail:")
    for b in outcome.bar_path:
        flags = []
        if b["tp_touch"]:
            flags.append("TP")
        if b["sl_touch"]:
            flags.append("SL")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        print(f"    {b['date']}: H={b['high']:.2f} L={b['low']:.2f} C={b['close']:.2f}{flag_str}")


if __name__ == "__main__":
    _main()
