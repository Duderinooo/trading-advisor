"""Backtest replay: closed trades × current gate logic → would-have-blocked report.

Purpose: when you tweak a gate threshold (e.g. MIN_CONFLUENCE_SCORE 5→6), this
script replays history to show how many *winners* would now be blocked vs.
how many *losers* avoided. Cheap proxy for parameter tuning without forward-test.

Limits: replay uses the data fields persisted on the closed trade itself, NOT a
fresh historical bar pull. Confluence score, RS-vs-index, vol-ratio etc. are
captured at entry-time on the rec; if old trades pre-date a gate, the gate is
counted as "unknown" rather than blocked.

CLI:
    ./venv/bin/python -m core.backtest             # full report
    ./venv/bin/python -m core.backtest --gate edge # filter to one gate
"""

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

import config
from config.setup_profiles import get_profile
from core.portfolio import load_portfolio, edge_ok

logger = logging.getLogger(__name__)


def _replay_trade(trade: dict) -> list[dict]:
    """Re-evaluate gates on a closed trade. Returns list of {gate, blocked, reason}."""
    out = []
    entry = float(trade.get("entry_price") or 0)
    sl = float(trade.get("stop_loss") or 0)
    tp = trade.get("take_profit")
    p_win = trade.get("p_win")
    setup = (trade.get("setup_type") or "").lower()
    conf_score = trade.get("confluence_score")
    rs = trade.get("rs_20d_vs_index_pct")
    vol_ratio = trade.get("volume_ratio")
    wk_trend = trade.get("wk_trend")
    direction = (trade.get("direction") or "LONG").upper()

    # Edge gate
    if isinstance(p_win, (int, float)) and entry and sl:
        ok, edge = edge_ok(p_win, entry, sl, tp)
        out.append({
            "gate": "edge", "blocked": not ok,
            "reason": f"edge={edge:.3f} vs MIN={config.MIN_EXPECTED_EDGE}",
        })

    # SL distance
    atr = trade.get("atr14") or trade.get("atr14_at_entry")
    if isinstance(atr, (int, float)) and atr > 0 and entry > sl > 0:
        d = (entry - sl) / atr
        if d < config.MIN_SL_DISTANCE_ATR:
            out.append({"gate": "sl_distance", "blocked": True,
                        "reason": f"sl={d:.2f}×ATR < {config.MIN_SL_DISTANCE_ATR}"})
        elif d > config.MAX_SL_DISTANCE_ATR:
            out.append({"gate": "sl_distance", "blocked": True,
                        "reason": f"sl={d:.2f}×ATR > {config.MAX_SL_DISTANCE_ATR}"})
        else:
            out.append({"gate": "sl_distance", "blocked": False, "reason": f"sl={d:.2f}×ATR"})

    profile = get_profile(setup)

    # Confluence
    if isinstance(conf_score, (int, float)):
        min_conf = max(3, config.MIN_CONFLUENCE_SCORE + profile.min_confluence_offset)
        out.append({
            "gate": "confluence", "blocked": conf_score < min_conf,
            "reason": f"score={conf_score} vs MIN={min_conf}",
        })

    # RS gate
    if isinstance(rs, (int, float)) and not profile.rs_override:
        out.append({
            "gate": "relative_strength", "blocked": rs < config.MIN_RS_20D_VS_INDEX_PCT,
            "reason": f"rs={rs:+.1f}pp vs MIN={config.MIN_RS_20D_VS_INDEX_PCT}pp",
        })

    # Breakout volume
    if profile.requires_breakout_volume and isinstance(vol_ratio, (int, float)):
        out.append({
            "gate": "breakout_volume",
            "blocked": vol_ratio < config.MIN_BREAKOUT_VOLUME_RATIO,
            "reason": f"vol_ratio={vol_ratio:.2f} vs MIN={config.MIN_BREAKOUT_VOLUME_RATIO}",
        })

    # Weekly trend
    if direction == "LONG" and wk_trend:
        out.append({
            "gate": "weekly_trend",
            "blocked": (wk_trend == "DOWN"),
            "reason": f"wk_trend={wk_trend}",
        })

    return out


def replay_all(closed_trades: list[dict] | None = None) -> dict:
    """Run replay on each closed trade. Aggregate stats per gate.

    Returns:
      {
        "n_trades": N,
        "n_winners": W, "n_losers": L,
        "gates": {
          gate_name: {
            "evaluated": int,           # trades where gate had data
            "would_block": int,         # blocked count
            "would_block_winners": int, # winners blocked = false-blocks (cost!)
            "would_block_losers": int,  # losers blocked = saves (benefit!)
          }
        }
      }
    """
    if closed_trades is None:
        closed_trades = load_portfolio().get("closed_trades", [])

    # Skip partials — they're scored separately and would double-count.
    final = [t for t in closed_trades if not t.get("partial")]
    n_winners = sum(1 for t in final if (t.get("pnl_pct") or 0) > 0)
    n_losers = len(final) - n_winners

    gate_stats: dict = defaultdict(lambda: {
        "evaluated": 0, "would_block": 0,
        "would_block_winners": 0, "would_block_losers": 0,
    })

    per_trade: list[dict] = []

    for t in final:
        is_win = (t.get("pnl_pct") or 0) > 0
        decisions = _replay_trade(t)
        per_trade.append({
            "ticker": t.get("ticker"),
            "entry_date": t.get("entry_date"),
            "outcome": "W" if is_win else "L",
            "pnl_pct": t.get("pnl_pct"),
            "decisions": decisions,
        })
        for d in decisions:
            g = d["gate"]
            gate_stats[g]["evaluated"] += 1
            if d["blocked"]:
                gate_stats[g]["would_block"] += 1
                if is_win:
                    gate_stats[g]["would_block_winners"] += 1
                else:
                    gate_stats[g]["would_block_losers"] += 1

    return {
        "n_trades": len(final),
        "n_winners": n_winners,
        "n_losers": n_losers,
        "gates": dict(gate_stats),
        "per_trade": per_trade,
    }


def write_report(report: dict, path: Path | None = None):
    """Write the replay report to backtest_report.json (consumed by web /api/backtest)."""
    path = path or Path(__file__).resolve().parent.parent / "backtest_report.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2)


def _format(report: dict) -> str:
    lines = [
        f"Trades: {report['n_trades']} (W {report['n_winners']} / L {report['n_losers']})",
        "",
        f"{'Gate':<20} {'Eval':>6} {'Block':>6} {'BlockW':>7} {'BlockL':>7} {'Net':>6}",
    ]
    for g, s in sorted(report["gates"].items()):
        net = s["would_block_losers"] - s["would_block_winners"]
        lines.append(
            f"{g:<20} {s['evaluated']:>6} {s['would_block']:>6} "
            f"{s['would_block_winners']:>7} {s['would_block_losers']:>7} {net:>+6}"
        )
    lines.append("")
    lines.append("Net = saved-losses − blocked-winners. Positive = gate paid rent.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", help="filter to one gate", default=None)
    ap.add_argument("--write", action="store_true", help="write report JSON for web")
    args = ap.parse_args()

    rep = replay_all()
    if args.gate:
        rep["gates"] = {k: v for k, v in rep["gates"].items() if k == args.gate}
    print(_format(rep))
    if args.write:
        write_report(rep)
        print("\nReport written to backtest_report.json")


if __name__ == "__main__":
    main()
