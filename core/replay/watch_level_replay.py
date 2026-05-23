"""Watch-level replay: did a Sonnet morning watch-level trigger within N days?

Walks daily bars from target_date forward and returns the first day the watch
would have fired per detect_events proximity + confirm-close + zone-mode logic.

Limits (vs live core/events/watchlevels.detect_events):
- Daily-bar granularity: uses bar.high/low for proximity. Intraday-precise
  touch order not resolved here (negligible for the diagnostic use case).
- Volume / VWAP anomaly gates NOT replayed: daily-bar lacks those snapshots.
  Live detection has anti-fake-breakout + flash-spike checks; this replay
  answers "would the price ever have approached the level?", not "would all
  guards pass". Reverse-funnel order: replay says YES → live MIGHT have fired.

CLI:
    ./venv/bin/python -m core.replay.watch_level_replay \\
        --ticker BAS.DE --type breakout_long --trigger 53.50 \\
        --date 2026-05-12 [--confirm 53.80] [--zone-low 52.5 --zone-high 54.0]
        [--window 5]
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import config
from core.data.historical import get_daily_bars_range


logger = logging.getLogger(__name__)


_LONG_BREAKOUT_TYPES = {"breakout_long", "breakout_resistance", "breakout"}


@dataclass
class WatchHitResult:
    triggered: bool
    hit_date: str | None
    hit_price: float | None      # bar.close on the hit day (approximation)
    hit_bar_high: float | None
    hit_bar_low: float | None
    reason: str                  # "proximity_line" / "zone_entry" / "confirm_close" / "no_hit" / "no_data"
    bars_walked: int


def replay_watch_level(
    ticker: str,
    start_date: date,
    *,
    trigger_price: float,
    level_type: str = "breakout_long",
    confirm_close_above: float | None = None,
    zone_low: float | None = None,
    zone_high: float | None = None,
    window_days: int = 5,
) -> WatchHitResult:
    """Walk daily bars from `start_date` for up to `window_days` trading days.
    Return the first bar that would have triggered the watch.

    Zone-mode (zone_low + zone_high both set + zone_low < zone_high) replaces
    the line proximity check: any bar with `zone_low ≤ low/close ≤ zone_high`
    counts as an entry into the zone.
    """
    end_date = start_date + timedelta(days=int(window_days * 1.6) + 3)
    bars = get_daily_bars_range(ticker, start_date, end_date)
    if not bars:
        return WatchHitResult(
            triggered=False, hit_date=None, hit_price=None,
            hit_bar_high=None, hit_bar_low=None,
            reason="no_data", bars_walked=0,
        )
    bars = bars[:window_days]

    zone_mode = (
        isinstance(zone_low, (int, float))
        and isinstance(zone_high, (int, float))
        and zone_low < zone_high
    )
    is_long_breakout = level_type.lower() in _LONG_BREAKOUT_TYPES

    for idx, bar in enumerate(bars, start=1):
        high = bar["high"]
        low = bar["low"]
        close = bar["close"]

        if zone_mode:
            # Bar enters the zone if any of (high/low/close) falls in [zone_low, zone_high]
            # Conservative: require close inside zone OR (high above zone_low AND low below zone_high)
            in_zone = (
                zone_low <= close <= zone_high
                or (high >= zone_low and low <= zone_high)
            )
            if in_zone:
                return WatchHitResult(
                    triggered=True, hit_date=bar["date"], hit_price=close,
                    hit_bar_high=high, hit_bar_low=low,
                    reason="zone_entry", bars_walked=idx,
                )
            continue

        # Line-mode proximity: |high − trigger| / trigger ≤ BREAKOUT_TRIGGER_PERCENT
        # For longs: also require high ≥ trigger (direction-aware).
        prox_pct = abs(high - trigger_price) / trigger_price * 100
        in_proximity = prox_pct <= config.BREAKOUT_TRIGGER_PERCENT
        if not in_proximity:
            continue

        if is_long_breakout and high < trigger_price:
            # Long-breakout proximity-only fail: high never broke above trigger.
            continue

        if confirm_close_above is not None and confirm_close_above > 0:
            tolerance = (
                confirm_close_above * config.CONFIRM_CLOSE_TOLERANCE_PCT / 100
            )
            if close < (confirm_close_above - tolerance):
                continue
            reason = "confirm_close"
        else:
            reason = "proximity_line"

        return WatchHitResult(
            triggered=True, hit_date=bar["date"], hit_price=close,
            hit_bar_high=high, hit_bar_low=low,
            reason=reason, bars_walked=idx,
        )

    return WatchHitResult(
        triggered=False, hit_date=None, hit_price=None,
        hit_bar_high=None, hit_bar_low=None,
        reason="no_hit", bars_walked=len(bars),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Replay a watch-level against historical daily bars.",
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--type", default="breakout_long",
                        help="Level type (default: breakout_long)")
    parser.add_argument("--trigger", type=float, required=True)
    parser.add_argument("--date", required=True, help="Watch creation date YYYY-MM-DD")
    parser.add_argument("--confirm", type=float, default=None,
                        help="confirm_close_above threshold (optional)")
    parser.add_argument("--zone-low", type=float, default=None)
    parser.add_argument("--zone-high", type=float, default=None)
    parser.add_argument("--window", type=int, default=5,
                        help="Trading-day window (default 5)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    start = datetime.strptime(args.date, "%Y-%m-%d").date()
    r = replay_watch_level(
        args.ticker, start,
        trigger_price=args.trigger,
        level_type=args.type,
        confirm_close_above=args.confirm,
        zone_low=getattr(args, "zone_low"),
        zone_high=getattr(args, "zone_high"),
        window_days=args.window,
    )

    if args.json:
        print(json.dumps(r.__dict__, indent=2))
        return

    print(f"\nWatch-level replay: {args.ticker} {args.type} @ {args.trigger}")
    print(f"  Start:   {args.date}, window {args.window}d")
    print(f"  Result:  {'TRIGGERED' if r.triggered else 'NO HIT'}")
    print(f"  Reason:  {r.reason}")
    print(f"  Bars walked: {r.bars_walked}")
    if r.triggered:
        print(f"  Hit date:    {r.hit_date}")
        print(f"  Hit close:   €{r.hit_price:.2f}")
        print(f"  Bar HL:      [{r.hit_bar_low:.2f}, {r.hit_bar_high:.2f}]")


if __name__ == "__main__":
    _main()
