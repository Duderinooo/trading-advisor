"""Entry-rec handler: run the 23-gate pipeline + assemble persisted rec + alert.

handle_entry_recommendation delegates the engine-gate pipeline to
core.llm.handlers.gates and, on full pass, calls _assemble_entry_rec_and_alert
to stamp metadata + render the Telegram entry-alert.
"""

import logging
from datetime import datetime

import config
from notifier import send_notification as _notify
from memory import log_trade, MEMPALACE_AVAILABLE

from core.gate_log import log_gate
from core.llm.handlers.gates import (
    DecisionResult, GateContext, build_gate_context, run_entry_gates,
)


logger = logging.getLogger(__name__)


def handle_entry_recommendation(
    entry: dict,
    *,
    mode: str,
    market_data: dict,
    market_ctx: dict,
    regime: str,
    cash: float,
    model: str,
) -> dict | None:
    """Run the engine-gate pipeline + assemble the persisted rec on full pass.

    Returns the rec dict (with status/regime/version stamps + Telegram message_id)
    on success, or None when any gate blocks. Mutates `entry` in-place during
    gate clamps (SL widening, size shrinking, TP auto-split, etc.).
    """
    gctx = build_gate_context(
        entry, mode=mode, market_data=market_data, market_ctx=market_ctx,
        regime=regime, cash=cash, model=model,
    )
    result = run_entry_gates(entry, gctx)
    _persist_decision(result)
    if not result.passed:
        return None
    return _assemble_entry_rec_and_alert(result.final_rec, gctx)


def _persist_decision(result: DecisionResult) -> None:
    """Append the DecisionResult audit-tree to analytics/decisions.jsonl. Used
    by the /audit Telegram command + dashboard tuning audits.
    Fail-soft: persistence errors never block the trading flow."""
    try:
        import json
        import os
        from pathlib import Path
        analytics_dir = (
            Path(os.path.dirname(os.path.abspath(__file__)))
            / ".." / ".." / ".." / ".." / "analytics"
        ).resolve()
        analytics_dir.mkdir(parents=True, exist_ok=True)
        out_path = analytics_dir / "decisions.jsonl"
        with out_path.open("a") as f:
            f.write(json.dumps(result.to_tree()) + "\n")
    except Exception:
        logger.exception("Decision persist failed for %s", result.ticker)


def _assemble_entry_rec_and_alert(entry: dict, gctx: GateContext) -> dict:
    """Stamp regime/version metadata, render the Telegram entry-alert, log to
    MemPalace. Called only after all engine gates pass."""
    log_gate(
        gctx.ticker, "all_passed", False, "entry approved",
        {"size_eur": entry.get("size_eur"), "conviction": entry.get("conviction"),
         "p_win": entry.get("p_win")},
    )
    vix_at_entry = (gctx.market_ctx.get("^VIX") or {}).get("price")
    spy = gctx.market_ctx.get("SPY5.DE") or {}
    spy_price = spy.get("price")
    spy_ma200 = spy.get("ma200")
    spy_above_ma200 = (
        bool(spy_price > spy_ma200)
        if isinstance(spy_price, (int, float)) and isinstance(spy_ma200, (int, float))
        else None
    )
    from core.portfolio import PROMPT_VERSION, STRATEGY_VERSION
    rec = {
        **entry,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending",
        "regime_at_entry": gctx.regime,
        "vix_at_entry": vix_at_entry if isinstance(vix_at_entry, (int, float)) else None,
        "spy_above_ma200": spy_above_ma200,
        "model": gctx.model,
        "prompt_version": PROMPT_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "decision_version": entry.get("decision_version") or PROMPT_VERSION,
    }
    ticker = rec.get("ticker", "?")
    entry_p = rec.get("entry_price", 0)
    sl = rec.get("stop_loss", 0)
    tp = rec.get("take_profit", [])
    size = rec.get("size_eur", 0)
    conv = rec.get("conviction", 0)
    hmin = rec.get("hold_days_min", "?")
    hmax = rec.get("hold_days_max", "?")
    thesis = rec.get("thesis", "")
    trail = rec.get("trailing_stop_pct")
    tp_str = " / ".join(f"€{t:.2f}" for t in (tp if isinstance(tp, list) else [tp]))
    trail_line = f"\nTrailing: {trail}%" if trail else ""
    capital = float(
        gctx.portfolio.get("total_capital_eur", config.BUDGET_EUR) or config.BUDGET_EUR
    )
    cash = float(gctx.portfolio.get("cash_eur", 0) or 0)
    shares_raw = size / entry_p if entry_p > 0 else 0.0
    if shares_raw >= 1:
        shares_prev = float(int(shares_raw))
        shares_str = f"{int(shares_prev)} Stk"
    else:
        shares_prev = round(shares_raw, 2)
        shares_str = f"{shares_prev:.2f} Stk (Bruchstück)"
    actual_size = round(shares_prev * entry_p, 2)
    risk_eur = (entry_p - sl) * shares_prev if entry_p > sl > 0 else 0.0
    risk_pct = (risk_eur / capital * 100) if capital > 0 else 0.0

    rt = rec.get("red_team_review") or {}
    rt_block = ""
    if rt:
        rt_modes = rt.get("top_failure_modes") or []
        rt_modes_str = "\n  • " + "\n  • ".join(rt_modes[:3]) if rt_modes else ""
        rt_conf = rt.get("confidence_thesis_holds")
        rt_verdict = rt.get("verdict") or "?"
        rt_emoji = "🐻" if rt_verdict == "WEAKEN" else "✅"
        rt_block = (
            f"\n{rt_emoji} *Red-Team*: {rt_verdict} (conf {rt_conf})"
            f"{rt_modes_str}\n"
        )

    wkn = config.TR_WKN_MAP.get(ticker)
    wkn_line = f"\n📱 TR-WKN: `{wkn}` (yfinance: {ticker})" if wkn else ""

    message_id = _notify(
        f"🎯 *ENTRY* | `{ticker}` | {shares_str} à €{entry_p:.2f} = €{actual_size:.2f}\n"
        f"Grund: {thesis}\n"
        f"Conv {conv}/5\n"
        f"SL €{sl:.2f} | TP {tp_str} | Risk €{risk_eur:.2f} ({risk_pct:.2f}% Kap.) | "
        f"Hold {hmin}-{hmax}d | Cash €{cash:.0f}{trail_line}"
        f"{wkn_line}"
        f"{rt_block}\n"
        f"_Reply `/confirm` (auto={shares_str}) oder `/confirm <stück> @<preis>` für override._",
        # Inline keyboard for phone-friendly quick actions. /confirm still
        # requires the price-quote reply since slippage gate needs a fill
        # price — so only the destructive paths (reject, downgrade-to-watch)
        # get buttons.
        buttons=[("❌ Reject", "rec:reject"), ("👁️ Watch", "rec:watch")],
    )
    if message_id:
        rec["message_id"] = message_id
    if MEMPALACE_AVAILABLE:
        log_trade(rec, "RECOMMENDED", rec.get("thesis", ""))
    logger.info("Entry recommendation: %s @ €%.2f (msg_id=%s)", ticker, entry_p, message_id)
    return rec
