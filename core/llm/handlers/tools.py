"""Tool-use handlers: process recommend_entry/add/update/exit calls from Claude.

Entry handler delegates the 23-gate engine pipeline to core.llm.handlers.gates;
this module only assembles the final rec dict + sends the Telegram alert on
full pass. Add/update/exit handlers are short enough to stay inline.

Helpers (red-team critique, thesis-decay confirmation, paper-portfolio mirror,
thesis-degradation diff, JSON dump/compact) also live here.

Stateless: handlers receive market_data + portfolio snapshot and return a
dict to persist (or None on block). The analyzer orchestrator does the actual
portfolio.json writes under the lock.
"""

import json
import logging
from datetime import datetime

from anthropic import Anthropic

import config
from notifier import send_notification as _notify, send_actionable
from memory import log_trade, MEMPALACE_AVAILABLE

from core.llm.telemetry.api_usage import increment_usage
from core.llm.telemetry.call_log import log_claude_call
from core.gate_log import log_gate
from core.llm.prompt.prompts import RED_TEAM_SYSTEM, RED_TEAM_TOOL
from core.portfolio import load_portfolio


logger = logging.getLogger(__name__)


# Lazily instantiated — only needed for red-team calls.
_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


# ---------- Token-saving JSON helpers (shared with analyzer) ----------

def compact(d):
    """Strip None and empty-string values recursively. Shrinks input tokens."""
    if isinstance(d, dict):
        return {k: compact(v) for k, v in d.items() if v is not None and v != ""}
    if isinstance(d, list):
        return [compact(x) for x in d]
    return d


def dump(d) -> str:
    """Compact JSON: no indent, no spaces, None stripped, UTF-8 preserved."""
    return json.dumps(compact(d), separators=(",", ":"), ensure_ascii=False)


# ---------- Thesis-degradation diff ----------

# Lower index = more bullish. analyst_rec_key downgrade = rank-increase ≥1.
_REC_KEY_RANK = {
    "strong_buy": 0, "buy": 1, "outperform": 1,
    "hold": 2, "neutral": 2,
    "underperform": 3, "sell": 4, "strong_sell": 4,
}


def build_thesis_degradation_lines(open_trades: list, market_data: dict) -> list[str]:
    """Per-position diff between frozen entry_snapshot and current market_data.

    Only surfaces DEGRADATIONS — improvements are noise here. Claude needs to know
    when a thesis-pillar broke (analyst flipped bearish, MA50 lost, wk_trend down,
    upside collapsed, RSI moved into reversal-zone vs entry).
    """
    out: list[str] = []
    for tr in open_trades or []:
        snap = tr.get("entry_snapshot") or {}
        if not snap:
            continue
        ticker = (tr.get("ticker") or "").upper()
        cur = market_data.get(ticker)
        if not isinstance(cur, dict) or cur.get("error"):
            continue

        flags: list[str] = []

        e_rec = (snap.get("analyst_rec_key") or "").lower()
        c_rec = (cur.get("analyst_rec_key") or "").lower()
        if e_rec in _REC_KEY_RANK and c_rec in _REC_KEY_RANK:
            if _REC_KEY_RANK[c_rec] - _REC_KEY_RANK[e_rec] >= 1:
                flags.append(f"DOWNGRADE Analyst {e_rec}→{c_rec}")

        e_up = snap.get("analyst_upside_pct")
        c_up = cur.get("analyst_upside_pct")
        if isinstance(e_up, (int, float)) and isinstance(c_up, (int, float)):
            if e_up - c_up >= 5.0:
                flags.append(f"DOWNGRADE Upside {e_up:+.1f}%→{c_up:+.1f}%")

        e_wk = (snap.get("wk_trend") or "").upper()
        c_wk = (cur.get("wk_trend") or "").upper()
        if e_wk == "UP" and c_wk in ("DOWN", "MIXED"):
            flags.append(f"DOWNGRADE wk_trend {e_wk}→{c_wk}")

        price = cur.get("price")
        e_ma50 = snap.get("ma50")
        c_ma50 = cur.get("ma50")
        if isinstance(price, (int, float)) and isinstance(e_ma50, (int, float)) and isinstance(c_ma50, (int, float)):
            entry_above = float(tr.get("entry_price") or 0) >= e_ma50
            if entry_above and price < c_ma50:
                flags.append(f"STRUCTURAL_BREAK MA50-Loss (€{price:.2f} < €{c_ma50:.2f})")

        e_rs = snap.get("rs_20d_vs_index_pct")
        c_rs = cur.get("rs_20d_vs_index_pct")
        if isinstance(e_rs, (int, float)) and isinstance(c_rs, (int, float)):
            if e_rs - c_rs >= 5.0:
                flags.append(f"DOWNGRADE RS_20d {e_rs:+.1f}pp→{c_rs:+.1f}pp")

        if flags:
            out.append(f"  {ticker}: " + " | ".join(flags))
    return out


# ---------- Red-team critic ----------

def run_red_team(rec: dict, snap: dict | None, regime: str, model: str) -> dict | None:
    """Bear-case critique of a proposed entry. Returns critique dict or None on failure.

    Single tool-forced Claude call. Cache key is the bear-critic system prompt
    (stable) — only the user-message changes per rec.
    """
    payload = {
        "ticker": rec.get("ticker"),
        "entry_price": rec.get("entry_price"),
        "stop_loss": rec.get("stop_loss"),
        "take_profit": rec.get("take_profit"),
        "size_eur": rec.get("size_eur"),
        "conviction": rec.get("conviction"),
        "p_win": rec.get("p_win"),
        "setup_type": rec.get("setup_type"),
        "top_fail_mode": rec.get("top_fail_mode"),
        "thesis": rec.get("thesis"),
        "confluence_score": rec.get("confluence_score"),
        "confluence_items": rec.get("confluence_items"),
        "correlations": rec.get("correlations"),
    }
    snap_slim = None
    if isinstance(snap, dict):
        keys = (
            "price", "prev_close", "change_pct", "rsi14", "macd", "macd_signal",
            "ma20", "ma50", "ma200", "atr14_pct", "volume_ratio", "spread_pct",
            "wk_trend", "rs_20d_vs_index_pct",
            "analyst_rec_key", "analyst_upside_pct", "analyst_count",
        )
        snap_slim = {k: snap.get(k) for k in keys if snap.get(k) is not None}

    user_msg = (
        "Kritisiere folgende Long-Empfehlung. Bear-Sicht. Tool-Call PFLICHT.\n\n"
        f"## Empfehlung\n{dump(payload)}\n\n"
        f"## Markt-Kontext für {payload['ticker']}\n{dump(snap_slim or {})}\n\n"
        f"## Regime\n{regime}"
    )

    try:
        resp = _get_client().messages.create(
            model=model,
            max_tokens=400,
            system=[{
                "type": "text",
                "text": RED_TEAM_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=[RED_TEAM_TOOL],
            tool_choice={"type": "tool", "name": "submit_critique"},
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        logger.warning("Red-team call failed: %s", e)
        return None

    increment_usage(forced=False)

    _rt_tool_calls = []
    _rt_data = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "submit_critique":
            _rt_data = block.input or {}
            _rt_tool_calls.append({"name": "submit_critique", "input": _rt_data})
            usage = getattr(resp, "usage", None)
            if usage is not None:
                logger.info(
                    "Red-team call: in=%s out=%s cache_read=%s cache_write=%s",
                    getattr(usage, "input_tokens", None),
                    getattr(usage, "output_tokens", None),
                    getattr(usage, "cache_read_input_tokens", None),
                    getattr(usage, "cache_creation_input_tokens", None),
                )

    log_claude_call(
        mode="red_team",
        model=model,
        turn=1,
        system_prompt=RED_TEAM_SYSTEM,
        user_message=user_msg,
        text_response="",
        tool_calls=_rt_tool_calls,
        usage=getattr(resp, "usage", None),
        extra={"target_ticker": payload.get("ticker")},
    )

    if _rt_data is not None:
        return _rt_data
    logger.warning("Red-team returned no tool_use block — skipping critique")
    return None


# ---------- Exit thesis-decay confirmation ----------

def exit_thesis_decay_confirmed(ticker: str, reason: str, market_data: dict) -> bool:
    """Multi-signal confirmation for thesis-decay exit-recs.

    Hard exits (SL-hit, earnings, TP, panic) always pass through. Thesis-decay
    exits need confirmation via three intraday-signals to avoid whipsaw-exits
    on transient weakness. Suppression triggers if ANY signal indicates "this
    is not a real exit-event": thin volume / oversold-bounce-zone / mild dip.

    Bot calls recommend_exit again next bar if signal persists — soft delay,
    not a hard block.
    """
    reason_lower = reason.lower()
    HARD_EXIT_MARKERS = (
        "sl-hit", "stop-loss", "stop loss", "sl hit",
        "earnings", "panic", "tp1", "tp2", "take-profit", "take profit",
    )
    if any(m in reason_lower for m in HARD_EXIT_MARKERS):
        return True

    md = market_data.get(ticker) or {}

    vol_ratio = md.get("volume_ratio")
    if isinstance(vol_ratio, (int, float)) and vol_ratio < config.EXIT_GATE_MIN_VOL_RATIO:
        log_gate(ticker, "exit_confirm_volume", True,
                 f"vol_ratio {vol_ratio:.2f} < {config.EXIT_GATE_MIN_VOL_RATIO} — thin selloff, whipsaw-risk",
                 {"vol_ratio": vol_ratio})
        logger.info("Exit suppress %s: vol_ratio %.2f below %.2f",
                    ticker, vol_ratio, config.EXIT_GATE_MIN_VOL_RATIO)
        return False

    rsi = md.get("rsi14")
    if isinstance(rsi, (int, float)) and rsi < config.EXIT_GATE_MIN_RSI:
        log_gate(ticker, "exit_confirm_rsi", True,
                 f"rsi14 {rsi:.1f} < {config.EXIT_GATE_MIN_RSI} — oversold-bounce-zone, wait",
                 {"rsi14": rsi})
        logger.info("Exit suppress %s: rsi %.1f below %.1f",
                    ticker, rsi, config.EXIT_GATE_MIN_RSI)
        return False

    vdev = md.get("vwap_dev_atr")
    if isinstance(vdev, (int, float)) and config.EXIT_GATE_MAX_VWAP_DEV_ATR < vdev < 0:
        log_gate(ticker, "exit_confirm_vwap", True,
                 f"vwap_dev {vdev:+.2f}×ATR mild — not panic-selling, await sustained break",
                 {"vwap_dev_atr": vdev})
        logger.info("Exit suppress %s: vwap_dev %.2f mild", ticker, vdev)
        return False

    return True


# ---------- Paper-portfolio auto-open ----------

def auto_paper_open(rec: dict) -> None:
    """Mirror a passing entry-rec into the paper portfolio for parallel learning.
    No Telegram, no MemPalace — paper trail stays out of user-facing history."""
    from core.portfolio import (
        load_paper_portfolio, save_paper_portfolio, paper_lock, build_trade_dict,
    )
    entry = float(rec.get("entry_price") or 0)
    size = float(rec.get("size_eur") or 0)
    shares = int(size / entry) if entry > 0 else 0
    if shares < 1:
        return
    fee = config.FIXED_FEE_EUR_PER_SIDE
    with paper_lock:
        pp = load_paper_portfolio()
        cost = shares * entry + fee
        cash = float(pp.get("cash_eur", 0) or 0)
        if cost > cash + 0.01:
            logger.info("paper: skip %s (cash €%.2f < cost €%.2f)",
                        rec.get("ticker"), cash, cost)
            return
        trade = build_trade_dict(
            rec, entry, float(shares), entry_snapshot=None, paper=True,
        )
        trade["entry_fee_eur"] = fee
        pp["cash_eur"] = round(cash - cost, 2)
        pp.setdefault("open_trades", []).append(trade)
        save_paper_portfolio(pp)
        logger.info(
            "paper: opened %s %d×€%.2f fee=€%.2f cash_now=€%.2f",
            trade["ticker"], shares, entry, fee, pp["cash_eur"],
        )



# ---------- Entry recommendation handler ----------

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

    Gate enforcement lives in core.llm.handlers.gates. Returns the rec dict
    (with status/regime/version stamps + Telegram message_id) on success, or
    None when any gate blocks. Mutates `entry` in-place during gate clamps."""
    from core.llm.handlers.gates import build_gate_context, run_entry_gates

    gctx = build_gate_context(
        entry, mode=mode, market_data=market_data, market_ctx=market_ctx,
        regime=regime, cash=cash, model=model,
    )
    passed = run_entry_gates(entry, gctx)
    if passed is None:
        return None
    return _assemble_entry_rec_and_alert(passed, gctx)


def _assemble_entry_rec_and_alert(entry: dict, gctx) -> dict:
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
        f"_Reply `/confirm` (auto={shares_str}) oder `/confirm <stück> @<preis>` für override._"
    )
    if message_id:
        rec["message_id"] = message_id
    if MEMPALACE_AVAILABLE:
        log_trade(rec, "RECOMMENDED", rec.get("thesis", ""))
    logger.info("Entry recommendation: %s @ €%.2f (msg_id=%s)", ticker, entry_p, message_id)
    return rec


# ---------- ADD recommendation handler ----------

def handle_add_recommendation(add: dict, market_data: dict) -> dict | None:
    """ADD (pyramiding) handler. Gates: position exists, valid size, drift, SL intact."""
    _at = (add.get("ticker") or "").upper()
    _add_size = float(add.get("additional_size_eur") or 0)
    _add_pf = load_portfolio()
    _open_pos = next(
        (tr for tr in _add_pf.get("open_trades", [])
         if (tr.get("ticker") or "").upper() == _at),
        None,
    )
    if not _open_pos:
        log_gate(_at, "add_no_position", True, "ADD on ticker without open position", {})
        logger.info("ADD suppressed: %s has no open position", _at)
        return None
    if _add_size <= 0:
        log_gate(_at, "add_invalid_size", True, f"additional_size_eur={_add_size}", {})
        logger.info("ADD suppressed: %s invalid size %s", _at, _add_size)
        return None

    _orig_entry = float(_open_pos.get("entry_price") or 0)
    _orig_size = float(_open_pos.get("size_eur") or 0)
    _orig_sl = float(_open_pos.get("stop_loss") or 0)
    _md = market_data.get(_at) or {}
    _curr = _md.get("price")
    _atr = _md.get("atr14")

    if isinstance(_curr, (int, float)) and isinstance(_atr, (int, float)) and _atr > 0:
        _drift = abs(float(_curr) - _orig_entry)
        if _drift > _atr * config.ADD_MAX_PRICE_DRIFT_ATR:
            log_gate(_at, "add_price_drift", True,
                     f"|curr-orig| {_drift:.2f} > {config.ADD_MAX_PRICE_DRIFT_ATR}×ATR ({_atr:.2f})",
                     {"curr": _curr, "orig": _orig_entry, "atr": _atr})
            logger.info(
                "ADD suppressed: %s price drift €%.2f > %.1f×ATR (%.2f) — fresh entry expected",
                _at, _drift, config.ADD_MAX_PRICE_DRIFT_ATR, _atr,
            )
            return None

    if isinstance(_curr, (int, float)) and _orig_sl > 0 and float(_curr) <= _orig_sl:
        log_gate(_at, "add_sl_breached", True,
                 f"price {_curr} ≤ original SL {_orig_sl}",
                 {"curr": _curr, "orig_sl": _orig_sl})
        logger.info("ADD suppressed: %s current %.2f ≤ original SL %.2f",
                    _at, _curr, _orig_sl)
        return None

    _trigger = (add.get("trigger") or "")[:120]
    _reinforce = (add.get("thesis_reinforcement") or "")[:120]
    _add_conv = add.get("conviction")
    add_msg_id = send_actionable(
        "ADD", _at, f"+€{_add_size:.0f}",
        reason=_trigger or _reinforce or "Setup-Verstärkung",
        conviction=_add_conv if isinstance(_add_conv, int) else None,
        extras={
            "Verstärkung": _reinforce,
            "Bestand": f"€{_orig_size:.0f} @ €{_orig_entry:.2f}",
            "SL": f"€{_orig_sl:.2f}",
        },
    )
    log_gate(_at, "add_all_passed", False, "ADD approved",
             {"add_size": _add_size, "orig_size": _orig_size})
    logger.info("ADD recommendation: %s +€%.2f (msg_id=%s)", _at, _add_size, add_msg_id)
    return {
        "kind": "add",
        "ticker": _at,
        "additional_size_eur": _add_size,
        "trigger": _trigger,
        "thesis_reinforcement": _reinforce,
        "conviction": _add_conv,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending",
        "message_id": add_msg_id,
    }


# ---------- UPDATE_POSITION_TARGETS handler ----------

def handle_update_targets(upd: dict) -> dict | None:
    """SL/TP update handler. Gates: position exists, has new value, has reason,
    SL stays below entry + can only move up, TP stays above entry."""
    _ut = (upd.get("ticker") or "").upper()
    _new_sl = upd.get("new_stop_loss")
    _new_tp = upd.get("new_take_profit")
    _ureason = (upd.get("reason") or "").strip()[:120]
    _upf = load_portfolio()
    _upos = next(
        (tr for tr in _upf.get("open_trades", [])
         if (tr.get("ticker") or "").upper() == _ut),
        None,
    )
    if not _upos:
        log_gate(_ut, "update_no_position", True,
                 "update_position_targets without open position", {})
        logger.info("Update suppressed: %s no open position", _ut)
        return None
    if _new_sl is None and _new_tp is None:
        log_gate(_ut, "update_empty", True, "neither SL nor TP set", {})
        logger.info("Update suppressed: %s neither SL nor TP set", _ut)
        return None
    if not _ureason:
        log_gate(_ut, "update_no_reason", True, "reason empty", {})
        logger.info("Update suppressed: %s no reason", _ut)
        return None

    _orig_entry = float(_upos.get("entry_price") or 0)
    _orig_sl = float(_upos.get("stop_loss") or 0)

    if _new_sl is not None:
        _new_sl = float(_new_sl)
        # SL only moves UP. Loosening protection = forbidden.
        if _new_sl >= _orig_entry:
            log_gate(_ut, "update_sl_above_entry", True,
                     f"new_sl {_new_sl} >= entry {_orig_entry}",
                     {"new_sl": _new_sl, "entry": _orig_entry})
            logger.info("Update suppressed: %s SL above entry", _ut)
            return None
        if _orig_sl > 0 and _new_sl < _orig_sl:
            log_gate(_ut, "update_sl_lowered", True,
                     f"new_sl {_new_sl} < orig_sl {_orig_sl} — loosening forbidden",
                     {"new_sl": _new_sl, "orig_sl": _orig_sl})
            logger.info("Update suppressed: %s SL lowered (loosening forbidden)", _ut)
            return None

    _tp_norm = None
    if _new_tp is not None:
        _tp_norm = _new_tp if isinstance(_new_tp, list) else [_new_tp]
        _tp_norm = [float(x) for x in _tp_norm]
        if any(x <= _orig_entry for x in _tp_norm):
            log_gate(_ut, "update_tp_below_entry", True,
                     f"tp {_tp_norm} has value ≤ entry {_orig_entry}",
                     {"new_tp": _tp_norm, "entry": _orig_entry})
            logger.info("Update suppressed: %s TP ≤ entry", _ut)
            return None

    _orig_tp = _upos.get("take_profit")
    _orig_tp_str = (
        " / ".join(f"€{t:.2f}" for t in _orig_tp) if isinstance(_orig_tp, list)
        else (f"€{_orig_tp:.2f}" if isinstance(_orig_tp, (int, float)) else "–")
    )
    _new_tp_str = (
        " / ".join(f"€{t:.2f}" for t in _tp_norm) if _tp_norm
        else "(unverändert)"
    )
    _new_sl_str = f"€{_new_sl:.2f}" if _new_sl is not None else "(unverändert)"
    _orig_sl_str = f"€{_orig_sl:.2f}" if _orig_sl > 0 else "–"
    upd_msg_id = _notify(
        f"🔧 *UPDATE* | `{_ut}`\n"
        f"Grund: {_ureason}\n"
        f"SL: {_orig_sl_str} → {_new_sl_str}\n"
        f"TP: {_orig_tp_str} → {_new_tp_str}\n"
        f"_Reply `/confirm` um zu übernehmen oder `/cancel`._"
    )
    log_gate(_ut, "update_all_passed", False, "SL/TP update approved",
             {"new_sl": _new_sl, "new_tp": _tp_norm})
    logger.info("Update recommendation: %s SL=%s TP=%s (msg_id=%s)",
                _ut, _new_sl, _tp_norm, upd_msg_id)
    return {
        "kind": "update",
        "ticker": _ut,
        "new_stop_loss": _new_sl,
        "new_take_profit": _tp_norm,
        "reason": _ureason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending",
        "message_id": upd_msg_id,
    }


# ---------- EXIT recommendation handler ----------

def handle_exit_recommendation(exit_rec: dict, market_data: dict) -> dict | None:
    """Exit handler. Gates: position exists, reason set, thesis-decay confirmed."""
    _et = (exit_rec.get("ticker") or "").upper()
    _ereason = (exit_rec.get("reason") or "").strip()[:120]
    _eurg = (exit_rec.get("urgency") or "today").lower()
    if _eurg not in {"now", "today", "eod"}:
        _eurg = "today"
    _epf = load_portfolio()
    _epos = next(
        (tr for tr in _epf.get("open_trades", [])
         if (tr.get("ticker") or "").upper() == _et),
        None,
    )
    if not _epos:
        log_gate(_et, "exit_no_position", True, "recommend_exit without open position", {})
        logger.info("Exit suppressed: %s no open position", _et)
        return None
    if not _ereason:
        log_gate(_et, "exit_no_reason", True, "reason empty", {})
        logger.info("Exit suppressed: %s no reason", _et)
        return None
    if not exit_thesis_decay_confirmed(_et, _ereason, market_data):
        logger.info("Exit suppressed: %s thesis-decay not confirmed", _et)
        return None

    _orig_entry = float(_epos.get("entry_price") or 0)
    _orig_size = float(_epos.get("size_eur") or 0)
    _curr_price = (market_data.get(_et) or {}).get("price")
    _curr_str = f"€{_curr_price:.2f}" if isinstance(_curr_price, (int, float)) else "–"
    _pnl_pct = (
        ((_curr_price - _orig_entry) / _orig_entry * 100)
        if isinstance(_curr_price, (int, float)) and _orig_entry > 0 else None
    )
    _pnl_str = f"{_pnl_pct:+.2f}%" if _pnl_pct is not None else "?"
    _urgency_emoji = {"now": "🚨", "today": "⚠️", "eod": "🕐"}.get(_eurg, "⚠️")
    _exit_wkn = config.TR_WKN_MAP.get(_et)
    _exit_wkn_line = f"\n📱 TR-WKN: `{_exit_wkn}`" if _exit_wkn else ""
    exit_msg_id = _notify(
        f"🎯 *EXIT* | `{_et}` | {_urgency_emoji} {_eurg}\n"
        f"Grund: {_ereason}\n"
        f"Bestand: €{_orig_size:.0f} @ €{_orig_entry:.2f} | Jetzt: {_curr_str} ({_pnl_str})"
        f"{_exit_wkn_line}\n"
        f"_Reply `/confirm` um auf TR zu schließen, dann `/close {_et} @PREIS [#tag]`._"
    )
    log_gate(_et, "exit_all_passed", False, "exit recommended", {"urgency": _eurg})
    logger.info("Exit recommendation: %s urgency=%s (msg_id=%s)",
                _et, _eurg, exit_msg_id)
    return {
        "kind": "exit",
        "ticker": _et,
        "reason": _ereason,
        "urgency": _eurg,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending",
        "message_id": exit_msg_id,
    }
