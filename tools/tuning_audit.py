"""Tuning-audit: detect config/ constant changes + auto-run replay-compare.

Manual review tool for the CLAUDE.md "no tuning on N=1" rule. When you change
a config constant, run this BEFORE committing to see the P&L delta over a
historical window.

What it does:
1. Parses `git diff [--rev REV] -- config/` to find changed constants
2. For each numeric constant change, applies the OLD value as override in
   replay_with_overrides over the last N trading days
3. Prints baseline-vs-variant P&L delta + per-day breakdown

Limits:
- Only detects single-line `NAME = VALUE` constant changes (not multi-line
  dicts, not list mutations). Sets/dicts/etc. fall through to a warning.
- Only meaningful for tunables that affect SL/TP-loop behavior
  (PARTIAL_TP_FRACTION, FIXED_FEE_EUR_PER_SIDE). Gate-threshold changes
  (MIN_EXPECTED_EDGE, MIN_RS_20D_VS_INDEX_PCT) won't produce a delta —
  use core/backtest.py for those.

CLI:
    # Diff working tree vs HEAD (default)
    ./venv/bin/python -m tools.tuning_audit --days 30

    # Diff against an older commit
    ./venv/bin/python -m tools.tuning_audit --rev HEAD~5 --days 60
"""

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


logger = logging.getLogger(__name__)


# Pattern: `NAME = VALUE  # optional-comment`
# Names are ALL_CAPS_WITH_UNDERSCORES (config convention).
_CONSTANT_LINE_RE = re.compile(
    r"^([+-])([A-Z][A-Z0-9_]*)\s*=\s*(.+?)\s*(?:#.*)?$"
)


@dataclass
class ConstantChange:
    name: str
    old_raw: str
    new_raw: str
    old_value: object | None    # parsed (int/float) or None if non-numeric
    new_value: object | None


def _safe_parse_value(raw: str) -> object | None:
    """Parse a numeric/bool/string literal. Returns None if it's a container
    (dict, list, set) or anything we can't safely eval."""
    s = raw.strip()
    if s.startswith(("{", "[", "(")):
        return None
    # Booleans + numerics + simple strings.
    if s in ("True", "False"):
        return s == "True"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    return None


def parse_diff(diff_text: str) -> list[ConstantChange]:
    """Pair `-NAME = X` with `+NAME = Y` lines into ConstantChange entries."""
    removed: dict[str, str] = {}
    added: dict[str, str] = {}
    for line in diff_text.splitlines():
        m = _CONSTANT_LINE_RE.match(line)
        if not m:
            continue
        sign, name, raw = m.group(1), m.group(2), m.group(3)
        if sign == "-":
            removed[name] = raw
        else:
            added[name] = raw

    changes: list[ConstantChange] = []
    for name in sorted(set(removed.keys()) & set(added.keys())):
        old_raw, new_raw = removed[name], added[name]
        if old_raw == new_raw:
            continue
        changes.append(ConstantChange(
            name=name,
            old_raw=old_raw,
            new_raw=new_raw,
            old_value=_safe_parse_value(old_raw),
            new_value=_safe_parse_value(new_raw),
        ))
    return changes


def get_config_diff(rev: str = "HEAD") -> str:
    """Run git diff against `rev` for config/ tree. Returns stdout string."""
    try:
        result = subprocess.run(
            ["git", "diff", rev, "--", "config/"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error("git diff failed: %s", e.stderr)
        return ""


def _audit_one_change(
    change: ConstantChange, days: int, portfolio: dict,
) -> dict:
    """Run replay-compare with OLD value as override (variant = pre-change state).
    Returns a dict suitable for printing."""
    from core.replay.compare import compare_to_baseline
    end = date.today()
    start = end - timedelta(days=days)
    overrides = {change.name: change.old_value}
    comparison = compare_to_baseline(overrides, start, end, portfolio)
    return {
        "constant": change.name,
        "old": change.old_value,
        "new": change.new_value,
        "raw_diff": f"{change.old_raw} → {change.new_raw}",
        "replay_window": f"{start} → {end} ({days}d)",
        **comparison,
    }


def _is_replayable(change: ConstantChange) -> bool:
    """Filter to constants whose change would actually surface in SL/TP replay."""
    if change.old_value is None or change.new_value is None:
        return False
    # Numeric-only (bool counts as int in Python; skip those for safety).
    if isinstance(change.old_value, bool) or isinstance(change.new_value, bool):
        return False
    return isinstance(change.old_value, (int, float)) and isinstance(
        change.new_value, (int, float),
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rev", default="HEAD",
        help="Git rev to diff against (default: HEAD = working tree vs last commit)",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Trading-day window for replay-compare (default 30)",
    )
    parser.add_argument(
        "--portfolio", default=None,
        help="Path to portfolio JSON. Default: live portfolio.json",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    diff_text = get_config_diff(args.rev)
    if not diff_text.strip():
        print(f"No config/ changes detected vs {args.rev}.")
        return 0

    changes = parse_diff(diff_text)
    if not changes:
        print(f"No constant changes detected vs {args.rev}.")
        print("(Multi-line dicts / list mutations not parsed.)")
        return 0

    replayable = [c for c in changes if _is_replayable(c)]
    non_replayable = [c for c in changes if not _is_replayable(c)]

    if non_replayable:
        print("⚠️  Non-replayable changes detected (use core/backtest.py for gate-tuning):")
        for c in non_replayable:
            print(f"    {c.name}: {c.old_raw} → {c.new_raw}")
        print()

    if not replayable:
        print("No replayable numeric constant changes — skipping SL/TP replay.")
        print("\nReminder (CLAUDE.md Tuning Rules):")
        print("  1. ≥20 outcome samples required")
        print("  2. Reference compute_hit_stats / gate_false_negative_rates")
        print("  3. Write research/YYYY-MM-DD-<topic>.md postmortem")
        print("  4. Add inline pointer comment next to constant")
        return 0

    # Load portfolio for replay.
    if args.portfolio:
        portfolio = json.loads(Path(args.portfolio).read_text())
    else:
        from core.portfolio import load_portfolio
        portfolio = load_portfolio()

    audits = [_audit_one_change(c, args.days, portfolio) for c in replayable]

    if args.json:
        print(json.dumps(audits, indent=2, default=str))
        return 0

    print("Tuning Audit — replay-compare each detected change:\n")
    for a in audits:
        print(f"━━ {a['constant']}: {a['raw_diff']} ━━")
        print(f"  Window:  {a['replay_window']}")
        print(f"  Baseline (new value, current): "
              f"€{a['baseline']['cumulative_pnl_eur']:+.2f}")
        print(f"  Variant  (old value, pre-change): "
              f"€{a['variant']['cumulative_pnl_eur']:+.2f}")
        delta = a["delta"]["pnl_eur"]
        marker = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
        # Interpretation note: variant = OLD value, so positive delta means
        # OLD was BETTER (i.e. you're making it WORSE).
        sign_word = "WORSE" if delta > 0 else ("BETTER" if delta < 0 else "EQUAL")
        print(f"  Δ P&L (old-vs-new): {marker} €{delta:+.2f}  "
              f"({sign_word} after change)")
        print()

    print("Reminder: a single replay-window is N=1 evidence. Combine with:")
    print("  - compute_hit_stats() per-setup expectancy trends")
    print("  - gate_false_negative_rates() per-gate FNR")
    print("  - research/ postmortem documenting the rationale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
