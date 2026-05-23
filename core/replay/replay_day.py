"""Single-day SL/TP replay: given a portfolio snapshot + target date, simulate
the trading-day's SL/TP loop using historical daily bars.

Read-only: never writes to portfolio.json. Returns a structured DayReplayResult
with the events that would have fired + simulated P&L impact.

Use cases:
- Self-audit: "given yesterday's portfolio, what should have happened today?"
- Foundation for Phase D Stage 3 (parameter sweep): replay_range walks N days
  with mutated portfolio snapshots to feed the sweep.

CLI:
    ./venv/bin/python -m core.replay.replay_day --date 2026-05-23
    ./venv/bin/python -m core.replay.replay_day --date 2026-05-23 \\
        --portfolio path/to/snapshot.json

Limits:
- Daily-bar granularity only (no intraday tick-order). Ambiguous days (both
  SL + TP touched in same bar) recorded as "ambiguous" and trade stays open.
- Trailing-stop ratchet uses bar.close as the "current_price" proxy (good
  approximation for an EOD-driven advisor).
- Watch-level detection NOT included (needs vol_ratio/vwap_dev_atr which the
  daily-bar cache doesn't carry). Stage 3 follow-up.
"""

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from core.data.historical import get_daily_bar
from core.data.historical_intraday import first_touch_intraday


logger = logging.getLogger(__name__)


@dataclass
class DayReplayResult:
    """Outcome of one day's SL/TP replay against a portfolio snapshot."""
    date: str
    bars_fetched: int = 0             # tickers with bar data available
    bars_missing: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)  # one per SL/TP/partial-TP trigger
    realized_pnl_eur: float = 0.0
    closed_full: int = 0
    closed_partial: int = 0
    ambiguous: int = 0                # both SL+TP touched on the same bar
    still_open: int = 0
    starting_open_count: int = 0


def _next_take_profit(trade: dict) -> float | None:
    """Mirror of core.events.sltp._next_take_profit (kept pure for replay)."""
    tp = trade.get("take_profit")
    if tp is None:
        return None
    if isinstance(tp, list):
        return float(tp[0]) if tp else None
    return float(tp)


def replay_sltp_step(
    portfolio: dict, target_date: date,
) -> tuple[dict, DayReplayResult]:
    """Simulate one trading day's SL/TP loop against historical bars.

    `portfolio` is treated as an at-start-of-day snapshot. Returns:
    - mutated portfolio (deep copy — original untouched)
    - DayReplayResult with events + P&L
    """
    pf = deepcopy(portfolio)
    open_trades = pf.get("open_trades", []) or []
    result = DayReplayResult(date=target_date.isoformat())
    result.starting_open_count = len(open_trades)
    if not open_trades:
        return pf, result

    surviving: list[dict] = []
    for trade in open_trades:
        ticker = trade.get("ticker")
        if not ticker:
            surviving.append(trade)
            continue

        bar = get_daily_bar(ticker, target_date, auto_fetch=True)
        if bar is None:
            result.bars_missing.append(ticker)
            surviving.append(trade)
            continue
        result.bars_fetched += 1

        sl = trade.get("stop_loss")
        tp = _next_take_profit(trade)
        entry = float(trade.get("entry_price", 0) or 0)
        shares = float(trade.get("shares", 0) or 0)
        high = bar["high"]
        low = bar["low"]

        sl_hit = isinstance(sl, (int, float)) and low <= sl
        tp_hit = isinstance(tp, (int, float)) and high >= tp

        if sl_hit and tp_hit:
            # Daily-bar can't order touches — try intraday 15m resolution.
            first = first_touch_intraday(ticker, target_date, sl, tp)
            if first == "sl":
                sl_hit, tp_hit = True, False  # resolved: SL first
            elif first == "tp":
                sl_hit, tp_hit = False, True  # resolved: TP first
            else:
                # Still ambiguous (no intraday data, or 15m bar also straddles).
                result.events.append({
                    "type": "AMBIGUOUS",
                    "ticker": ticker, "sl": sl, "tp": tp,
                    "bar_high": high, "bar_low": low,
                })
                result.ambiguous += 1
                surviving.append(trade)
                continue

        if sl_hit:
            pnl_eur = (sl - entry) * shares if entry and shares else 0
            result.events.append({
                "type": "STOP_LOSS_HIT",
                "ticker": ticker, "stop_loss": sl,
                "exit_price": sl, "entry": entry, "shares": shares,
                "pnl_eur": round(pnl_eur, 2),
                "bar_date": bar["date"],
            })
            result.realized_pnl_eur += pnl_eur
            result.closed_full += 1
            # Don't append to surviving — trade closed
            continue

        if tp_hit:
            had_more_tps = (
                isinstance(trade.get("take_profit"), list)
                and len(trade["take_profit"]) > 1
            )
            if had_more_tps:
                # Partial: sell PARTIAL_TP_FRACTION at TP1, remainder runs.
                shares_to_sell = round(shares * config.PARTIAL_TP_FRACTION, 4)
                pnl_eur = (tp - entry) * shares_to_sell if entry else 0
                result.events.append({
                    "type": "PARTIAL_TP_HIT",
                    "ticker": ticker, "take_profit": tp,
                    "exit_price": tp, "entry": entry,
                    "shares_sold": shares_to_sell,
                    "shares_remaining": round(shares - shares_to_sell, 4),
                    "pnl_eur": round(pnl_eur, 2),
                    "bar_date": bar["date"],
                })
                result.realized_pnl_eur += pnl_eur
                result.closed_partial += 1
                # Mutate trade: reduce shares, pop TP1, move SL to BE
                trade["shares"] = round(shares - shares_to_sell, 4)
                trade["size_eur"] = round(trade["shares"] * entry, 2) if entry else 0.0
                trade["take_profit"].pop(0)
                if not trade["take_profit"]:
                    trade["take_profit"] = None
                # BE shift with fee-buffer (mirrors live sltp.py behavior)
                shares_remaining = trade["shares"]
                be_buffer = (
                    config.FIXED_FEE_EUR_PER_SIDE / shares_remaining
                    if shares_remaining > 0 else 0.0
                )
                be_target = round(entry + be_buffer, 2) if entry else 0
                if entry and (trade.get("stop_loss") is None
                              or trade["stop_loss"] < be_target):
                    trade["stop_loss"] = be_target
                if trade["shares"] > 0:
                    surviving.append(trade)
            else:
                # Final TP: close full
                pnl_eur = (tp - entry) * shares if entry else 0
                result.events.append({
                    "type": "TAKE_PROFIT_HIT",
                    "ticker": ticker, "take_profit": tp,
                    "exit_price": tp, "entry": entry, "shares": shares,
                    "pnl_eur": round(pnl_eur, 2),
                    "bar_date": bar["date"],
                })
                result.realized_pnl_eur += pnl_eur
                result.closed_full += 1
            continue

        # Neither hit → trade stays open
        surviving.append(trade)

    pf["open_trades"] = surviving
    result.still_open = len(surviving)
    result.realized_pnl_eur = round(result.realized_pnl_eur, 2)
    return pf, result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Replay one trading day's SL/TP loop against historical bars.",
    )
    parser.add_argument("--date", required=True, help="Target trading date YYYY-MM-DD")
    parser.add_argument(
        "--portfolio", default=None,
        help="Path to portfolio JSON snapshot. Default: live portfolio.json",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    if args.portfolio:
        portfolio = json.loads(Path(args.portfolio).read_text())
    else:
        from core.portfolio import load_portfolio
        portfolio = load_portfolio()

    pf_after, result = replay_sltp_step(portfolio, target_date)

    if args.json:
        out = {
            **{k: v for k, v in result.__dict__.items()},
            "open_after": [
                {"ticker": t.get("ticker"), "shares": t.get("shares"),
                 "stop_loss": t.get("stop_loss"), "take_profit": t.get("take_profit")}
                for t in pf_after.get("open_trades", []) or []
            ],
        }
        print(json.dumps(out, indent=2))
        return

    # Human-readable summary
    print(f"\nReplay {result.date}")
    print(f"  Starting open: {result.starting_open_count}")
    print(f"  Bars fetched:  {result.bars_fetched}")
    if result.bars_missing:
        print(f"  Bars missing:  {', '.join(result.bars_missing)}")
    print(f"  Realized P&L:  €{result.realized_pnl_eur:+.2f}")
    print(f"  Full closes:   {result.closed_full}")
    print(f"  Partials:      {result.closed_partial}")
    print(f"  Ambiguous:     {result.ambiguous}")
    print(f"  Still open:    {result.still_open}")
    if result.events:
        print("\n  Events:")
        for e in result.events:
            t = e["ticker"]
            etype = e["type"]
            if etype in ("STOP_LOSS_HIT", "TAKE_PROFIT_HIT"):
                print(f"    {etype} {t} @ €{e['exit_price']:.2f} "
                      f"(P&L €{e['pnl_eur']:+.2f})")
            elif etype == "PARTIAL_TP_HIT":
                print(f"    PARTIAL_TP {t} @ €{e['exit_price']:.2f} "
                      f"sold {e['shares_sold']}, remain {e['shares_remaining']} "
                      f"(P&L €{e['pnl_eur']:+.2f})")
            elif etype == "AMBIGUOUS":
                print(f"    AMBIGUOUS {t}: bar [{e['bar_low']:.2f}, "
                      f"{e['bar_high']:.2f}] touched both SL {e['sl']} + TP {e['tp']}")


if __name__ == "__main__":
    _main()
