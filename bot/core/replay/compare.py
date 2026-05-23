"""Parameter sweep wrapper for replay_range.

Runs replay_range twice — baseline (current config) + variant (with overrides) —
and reports the P&L delta. Useful for evaluating tuning changes before merging:
"what would PARTIAL_TP_FRACTION=0.7 have produced on last month's positions?"

Only tunables that affect SL/TP-loop behavior produce a measurable delta:
- PARTIAL_TP_FRACTION (how much closed at TP1)
- FIXED_FEE_EUR_PER_SIDE (affects BE-shift buffer)

Gate-threshold tuning (MIN_EXPECTED_EDGE, MIN_RS_20D_VS_INDEX_PCT, etc.) doesn't
affect SL/TP replay since entries are historical. For gate-tuning see
core/backtest.py (replays closed_trades against gates).

CLI:
    ./venv/bin/python -m core.replay.compare \\
        --start 2026-05-12 --end 2026-05-23 \\
        --override PARTIAL_TP_FRACTION=0.7
"""

import json
import logging
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import config
from core.replay.replay_range import RangeReplayResult, replay_range


logger = logging.getLogger(__name__)


def _coerce(value: str):
    """Best-effort type coercion for CLI overrides: int → float → str."""
    try:
        if "." not in value and "e" not in value.lower():
            return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def replay_with_overrides(
    overrides: dict, start: date, end: date, portfolio: dict,
) -> tuple[dict, RangeReplayResult]:
    """Run replay_range with temporary config-attribute overrides.

    Saves + restores original values around the call. Unknown attributes raise
    AttributeError (typo-safety)."""
    saved: dict[str, object] = {}
    try:
        for name, value in overrides.items():
            if not hasattr(config, name):
                raise AttributeError(
                    f"config.{name} not found — refusing to apply override"
                )
            saved[name] = getattr(config, name)
            setattr(config, name, value)
        return replay_range(start, end, portfolio)
    finally:
        for name, original in saved.items():
            setattr(config, name, original)


def compare_to_baseline(
    overrides: dict, start: date, end: date, portfolio: dict,
) -> dict:
    """Run baseline + variant replays, return summary delta."""
    baseline_pf, baseline = replay_range(start, end, deepcopy(portfolio))
    variant_pf, variant = replay_with_overrides(
        overrides, start, end, deepcopy(portfolio),
    )
    return {
        "overrides": overrides,
        "baseline": {
            "cumulative_pnl_eur": baseline.cumulative_pnl_eur,
            "closed_full": baseline.total_closed_full,
            "closed_partial": baseline.total_closed_partial,
            "ambiguous": baseline.total_ambiguous,
            "ending_open": baseline.ending_open_count,
        },
        "variant": {
            "cumulative_pnl_eur": variant.cumulative_pnl_eur,
            "closed_full": variant.total_closed_full,
            "closed_partial": variant.total_closed_partial,
            "ambiguous": variant.total_ambiguous,
            "ending_open": variant.ending_open_count,
        },
        "delta": {
            "pnl_eur": round(
                variant.cumulative_pnl_eur - baseline.cumulative_pnl_eur, 2,
            ),
            "closed_full": variant.total_closed_full - baseline.total_closed_full,
            "closed_partial": (
                variant.total_closed_partial - baseline.total_closed_partial
            ),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Compare baseline vs variant config in SL/TP replay.",
    )
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--override", action="append", required=True,
        help="KEY=VALUE config override (repeat for multiple). E.g. "
             "--override PARTIAL_TP_FRACTION=0.7",
    )
    parser.add_argument(
        "--portfolio", default=None,
        help="Path to portfolio JSON. Default: live portfolio.json",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    overrides: dict = {}
    for kv in args.override:
        if "=" not in kv:
            parser.error(f"Bad override (missing '='): {kv}")
        k, v = kv.split("=", 1)
        overrides[k.strip()] = _coerce(v.strip())

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if args.portfolio:
        portfolio = json.loads(Path(args.portfolio).read_text())
    else:
        from core.portfolio import load_portfolio
        portfolio = load_portfolio()

    comparison = compare_to_baseline(overrides, start, end, portfolio)

    if args.json:
        print(json.dumps(comparison, indent=2))
        return

    print(f"\nCompare {args.start} → {args.end}")
    print(f"  Overrides: {overrides}")
    print(f"\n  Baseline: €{comparison['baseline']['cumulative_pnl_eur']:+.2f}  "
          f"(full={comparison['baseline']['closed_full']}, "
          f"partial={comparison['baseline']['closed_partial']})")
    print(f"  Variant:  €{comparison['variant']['cumulative_pnl_eur']:+.2f}  "
          f"(full={comparison['variant']['closed_full']}, "
          f"partial={comparison['variant']['closed_partial']})")
    print(f"  Δ P&L:    €{comparison['delta']['pnl_eur']:+.2f}")


if __name__ == "__main__":
    _main()
