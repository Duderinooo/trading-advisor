"""Tool-use handlers: process recommend_entry/add/update/exit calls from Claude.

Owns all engine-gate enforcement on entry recs (14 gates), plus the smaller
add/update/exit flows. Helper functions for red-team critique, thesis-decay
confirmation, paper-portfolio mirror, and thesis-degradation diff live here too.

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

from core.api_usage import increment_usage
from core.call_log import log_claude_call
from core.gate_log import log_gate
from core.prompts import RED_TEAM_SYSTEM, RED_TEAM_TOOL
from core.portfolio import (
    portfolio_lock, load_portfolio, suggest_position_size,
    compute_hit_stats, compute_sector_exposure,
    risk_halt_status, edge_ok, compute_confluence,
    compute_correlations, dd_scaling_factor,
    record_entry_gate_cooldown,
)
from core.market_data import get_earnings_warnings, get_returns


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


# ---------- Entry recommendation handler (14 engine gates) ----------

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
    """Run all engine gates on a recommend_entry tool-call.

    Returns persisted rec dict on success (with status/regime/version stamps +
    message_id from Telegram alert), or None when any gate blocks. Mutates
    `entry` dict in-place when clamping (SL widening, size shrinking).
    """
    # ---- Validation: required fields (catches truncated tool_use) ----
    _required = ("ticker", "entry_price", "stop_loss", "take_profit",
                 "size_eur", "conviction", "p_win", "thesis",
                 "setup_type", "top_fail_mode")
    _missing = [k for k in _required if not entry.get(k)]
    if _missing:
        log_gate(
            (entry.get("ticker") or "?").upper(),
            "incomplete_rec", True,
            f"recommend_entry missing required fields: {','.join(_missing)} — "
            f"likely max_tokens truncation",
            {"missing": _missing, "received_keys": sorted(entry.keys())},
        )
        logger.error(
            "recommend_entry DROPPED: %s missing %s (truncated tool_use?)",
            entry.get("ticker"), _missing,
        )
        return None

    # ---- Already-open silent drop (pyramiding goes via recommend_add) ----
    _t = (entry.get("ticker") or "").upper()
    _open_tickers = {
        (tr.get("ticker") or "").upper()
        for tr in load_portfolio().get("open_trades", [])
    }
    if _t in _open_tickers:
        log_gate(
            _t, "already_open", True,
            "ticker has open position — re-entry suppressed (use recommend_add_to_position)",
            {},
        )
        logger.info("Entry suppressed: %s already open (Claude should use recommend_add)", _t)
        return None

    # ---- Sonnet→Haiku event-watch coupling ----
    if mode == "event":
        _watch_match = next(
            (w for w in load_portfolio().get("watch_levels", [])
             if (w.get("ticker") or "").upper() == _t),
            None,
        )
        if not _watch_match:
            logger.warning(
                "Entry BLOCKED by event-watch-coupling: %s has no morning watch_level", _t,
            )
            log_gate(
                _t, "event_watch_coupling", True,
                "no morning watch_level for event-mode entry",
                {"ticker": _t},
            )
            return None
        # Stamp morning thesis so downstream carries Sonnet-vetted version.
        morning_thesis = _watch_match.get("thesis")
        if morning_thesis:
            entry["watch_thesis"] = morning_thesis

    # ---- Risk halt (kill-switch, daily loss, drawdown, heat) ----
    _pf_snapshot = load_portfolio()
    _halt = risk_halt_status(_pf_snapshot)
    if _halt["halt"]:
        reason = " | ".join(_halt["reasons"])
        logger.warning("Entry BLOCKED by risk halt: %s", reason)
        log_gate(_t, "risk_halt", True, reason, _halt.get("metrics"))
        if "risk_halt" in config.GATE_BLOCK_NOTIFY_WHITELIST:
            _notify(f"⛔ *ENTRY BLOCKIERT* ({_t})\n{reason}")
        return None

    # ---- Regime gate ----
    if config.RISK_OFF_BLOCKS_LONGS and regime.startswith("RISK_OFF"):
        direction = str(entry.get("direction") or "LONG").upper()
        if direction == "LONG":
            logger.warning("Entry BLOCKED by regime gate: RISK_OFF + LONG")
            log_gate(_t, "regime", True,
                     f"RISK_OFF + LONG (regime={regime})", {"regime": regime})
            return None

    # ---- No-entry-zone (open/close noise windows) ----
    _now = datetime.now()
    _now_min = _now.hour * 60 + _now.minute
    _blocked_window = None
    for sh, sm, eh, em in config.NO_ENTRY_WINDOWS:
        if sh * 60 + sm <= _now_min < eh * 60 + em:
            _blocked_window = f"{sh:02d}:{sm:02d}–{eh:02d}:{em:02d}"
            break
    if _blocked_window:
        logger.warning("Entry BLOCKED by no-entry-zone: %s in window %s", _t, _blocked_window)
        log_gate(_t, "no_entry_zone", True,
                 f"window {_blocked_window}", {"window": _blocked_window})
        return None

    # ---- Extended-UP-Day gate (chase-protection, asymmetric) ----
    _snap_md = market_data.get(_t) or {}
    _change_pct = _snap_md.get("change_pct")
    _atr_pct = _snap_md.get("atr14_pct")
    if (isinstance(_change_pct, (int, float))
            and isinstance(_atr_pct, (int, float)) and _atr_pct > 0
            and _change_pct > 1.5 * _atr_pct):
        logger.warning(
            "Entry BLOCKED by extended-UP-day gate: %s change=%+.2f%% > 1.5×ATR%%=%.2f%%",
            _t, _change_pct, 1.5 * _atr_pct,
        )
        log_gate(_t, "extended_up_day", True,
                 f"change_pct {_change_pct:+.2f}% > 1.5×atr14_pct ({1.5 * _atr_pct:.2f}%)",
                 {"change_pct": _change_pct, "atr14_pct": _atr_pct,
                  "threshold_pct": 1.5 * _atr_pct})
        return None

    # ---- SL-distance sanity (clamp too-tight, reject too-wide) ----
    _entry_p = float(entry.get("entry_price") or 0)
    _sl = float(entry.get("stop_loss") or 0)
    _atr = _snap_md.get("atr14")
    if _entry_p > _sl > 0 and isinstance(_atr, (int, float)) and _atr > 0:
        _sl_dist_atr = (_entry_p - _sl) / _atr
        if _sl_dist_atr < config.MIN_SL_DISTANCE_ATR:
            _clamped_sl = round(_entry_p - config.MIN_SL_DISTANCE_ATR * _atr, 2)
            logger.warning(
                "SL clamped (too tight): %s %.2f→%.2f (%.2f×ATR → %.2f×ATR)",
                _t, _sl, _clamped_sl, _sl_dist_atr, config.MIN_SL_DISTANCE_ATR,
            )
            log_gate(_t, "sl_distance", False,
                     f"SL clamped {_sl_dist_atr:.2f}×ATR → {config.MIN_SL_DISTANCE_ATR}×ATR",
                     {"sl_dist_atr": round(_sl_dist_atr, 2), "kind": "tight_clamped",
                      "sl_from": _sl, "sl_to": _clamped_sl})
            entry["stop_loss"] = _clamped_sl
        elif _sl_dist_atr > config.MAX_SL_DISTANCE_ATR:
            logger.warning(
                "Entry BLOCKED by SL-too-wide: %s SL %.2f×ATR > %.2f×ATR",
                _t, _sl_dist_atr, config.MAX_SL_DISTANCE_ATR,
            )
            log_gate(_t, "sl_distance", True,
                     f"SL {_sl_dist_atr:.2f}×ATR > {config.MAX_SL_DISTANCE_ATR}",
                     {"sl_dist_atr": round(_sl_dist_atr, 2), "kind": "wide"})
            return None

    # ---- Edge gate (with Brier-haircut, cap ±0.20) ----
    _HAIRCUT_CAP = 0.20
    _p_raw = entry.get("p_win")
    _stats = compute_hit_stats(
        _pf_snapshot.get("closed_trades", []), _pf_snapshot.get("cash_movements", []),
    )
    _haircut = 0.0
    if _stats and _stats.get("calibration"):
        _haircut = _stats["calibration"].get("haircut") or 0.0
    if isinstance(_p_raw, (int, float)) and _haircut != 0:
        _capped_haircut = max(-_HAIRCUT_CAP, min(_HAIRCUT_CAP, _haircut))
        _p_adj = max(0.01, min(0.99, _p_raw - _capped_haircut))
    else:
        _p_adj = _p_raw
    _ok, _edge = edge_ok(
        _p_adj, entry.get("entry_price"), entry.get("stop_loss"), entry.get("take_profit"),
    )
    if not _ok:
        logger.warning(
            "Entry BLOCKED by edge gate: edge=%.3f < %.3f (p_raw=%s, haircut=%s, p_adj=%s)",
            _edge, config.MIN_EXPECTED_EDGE, _p_raw, _haircut, _p_adj,
        )
        log_gate(
            _t, "edge", True,
            f"edge {_edge:.3f} < {config.MIN_EXPECTED_EDGE}",
            {"edge": round(_edge, 3), "p_raw": _p_raw, "p_adj": _p_adj, "haircut": _haircut},
        )
        record_entry_gate_cooldown(_t, "edge", f"edge {_edge:.3f} < {config.MIN_EXPECTED_EDGE}")
        return None

    # ---- Sector cluster gate ----
    _sector = config.SECTOR_MAP.get(_t)
    if _sector:
        _exposure = compute_sector_exposure(_pf_snapshot)
        _current = _exposure.get(_sector, [])
        if len(_current) >= config.MAX_POSITIONS_PER_SECTOR and _t not in _current:
            logger.warning(
                "Entry BLOCKED by sector gate: %s in %s, already %d open (%s)",
                _t, _sector, len(_current), ", ".join(_current),
            )
            log_gate(_t, "sector", True,
                     f"{_sector} {len(_current)}/{config.MAX_POSITIONS_PER_SECTOR}",
                     {"sector": _sector, "current": _current})
            return None

    # ---- Adaptive-Kelly clamp (modifier, not blocker) ----
    _p_clamp = entry.get("p_win")
    _entry_c = float(entry.get("entry_price") or 0)
    _sl_c = float(entry.get("stop_loss") or 0)
    _tp_c = entry.get("take_profit")
    _tp1 = _tp_c[0] if isinstance(_tp_c, list) and _tp_c else (
        _tp_c if isinstance(_tp_c, (int, float)) else None
    )
    if (isinstance(_p_clamp, (int, float)) and 0 < _p_clamp < 1
            and _entry_c > _sl_c > 0 and isinstance(_tp1, (int, float)) and _tp1 > _entry_c):
        _r2r = (_tp1 - _entry_c) / (_entry_c - _sl_c)
        _km = (_stats or {}).get("kelly_mult") or config.KELLY_FRACTION
        _atr_pct_kelly = _snap_md.get("atr14_pct")
        _kelly_cap = suggest_position_size(
            _atr_pct_kelly, cash, p_win=_p_clamp, reward_to_risk=_r2r, kelly_mult=_km,
        )
        _orig_size = float(entry.get("size_eur") or 0)
        if _orig_size > _kelly_cap > 0:
            entry["size_eur"] = _kelly_cap
            entry["kelly_clamp"] = {
                "kelly_mult": _km, "r2r": round(_r2r, 2),
                "original_size_eur": _orig_size, "capped_size_eur": _kelly_cap,
            }
            logger.warning(
                "Kelly-clamp: size €%.2f → €%.2f (kelly_mult=%.2f, p=%.2f, R:R=%.2f)",
                _orig_size, _kelly_cap, _km, _p_clamp, _r2r,
            )

    # ---- VIX size-dampening (modifier) ----
    _vix = (market_ctx.get("^VIX") or {}).get("price")
    _vix_factor = 1.0
    if isinstance(_vix, (int, float)):
        if _vix > 30:
            _vix_factor = 0.25
        elif _vix > 20:
            _vix_factor = 0.5
    if _vix_factor < 1.0:
        _orig = float(entry.get("size_eur") or 0)
        if _orig > 0:
            entry["size_eur"] = round(_orig * _vix_factor, 2)
            entry["vix_dampener"] = {
                "vix": _vix, "factor": _vix_factor, "original_size_eur": _orig,
            }
            logger.warning(
                "VIX-dampener: size €%.2f → €%.2f (VIX=%.2f, factor=%.2f)",
                _orig, entry["size_eur"], _vix, _vix_factor,
            )

    # ---- Weekly-trend gate ----
    _direction = str(entry.get("direction") or "LONG").upper()
    _wk = _snap_md.get("wk_trend")
    if _direction == "LONG" and _wk == "DOWN":
        logger.warning("Entry BLOCKED by weekly-trend gate: %s LONG vs wk_trend=DOWN", _t)
        log_gate(_t, "weekly_trend", True, "LONG vs wk_trend=DOWN", {"wk_trend": _wk})
        return None

    # ---- Earnings hard-block (T-N to T+0, override: earnings_drift) ----
    _setup = (entry.get("setup_type") or "").lower()
    if _setup != "earnings_drift":
        _ew = get_earnings_warnings([_t], days_ahead=config.EARNINGS_ENTRY_BLOCK_DAYS)
        if _ew:
            _w = _ew[0]
            logger.warning(
                "Entry BLOCKED by earnings gate: %s in %d Tag(en) (%s)",
                _t, _w["days_until"], _w["earnings_date"],
            )
            log_gate(_t, "earnings", True,
                     f"earnings in {_w['days_until']}d",
                     {"days_until": _w["days_until"], "earnings_date": _w["earnings_date"]})
            return None

    # ---- Relative-Strength gate (override: mean_rev / reversal / gap_fill / squeeze) ----
    _rs = _snap_md.get("rs_20d_vs_index_pct")
    _rs_override_setups = {
        "mean_reversion", "reversal_oversold", "gap_fill", "pre_breakout_squeeze",
    }
    if isinstance(_rs, (int, float)) and _setup not in _rs_override_setups:
        if _rs < config.MIN_RS_20D_VS_INDEX_PCT:
            logger.warning(
                "Entry BLOCKED by RS gate: %s rs_20d=%+.2fpp < %.2fpp (setup=%s)",
                _t, _rs, config.MIN_RS_20D_VS_INDEX_PCT, _setup,
            )
            log_gate(_t, "relative_strength", True,
                     f"rs_20d {_rs:+.1f}pp < {config.MIN_RS_20D_VS_INDEX_PCT}pp",
                     {"rs_20d": _rs, "setup": _setup})
            record_entry_gate_cooldown(_t, "relative_strength",
                                       f"rs_20d {_rs:+.1f}pp < {config.MIN_RS_20D_VS_INDEX_PCT}pp")
            return None

    # ---- Volume-Confirmation für Breakouts ----
    if _setup == "breakout_resistance":
        _vr = _snap_md.get("volume_ratio")
        if isinstance(_vr, (int, float)) and _vr < config.MIN_BREAKOUT_VOLUME_RATIO:
            logger.warning(
                "Entry BLOCKED by volume gate: %s breakout vol_ratio=%.2f < %.2f",
                _t, _vr, config.MIN_BREAKOUT_VOLUME_RATIO,
            )
            log_gate(_t, "breakout_volume", True,
                     f"vol_ratio {_vr:.2f} < {config.MIN_BREAKOUT_VOLUME_RATIO}",
                     {"vol_ratio": _vr})
            return None

    # ---- Confluence-Score gate (mean-rev family relaxed by 2) ----
    _conf = compute_confluence(_snap_md, regime) if _snap_md else {
        "score": 0, "items": {}, "missing": ["no_data"],
    }
    _min_conf = config.MIN_CONFLUENCE_SCORE
    if _setup in ("mean_reversion", "reversal_oversold", "gap_fill", "pre_breakout_squeeze"):
        _min_conf = max(3, config.MIN_CONFLUENCE_SCORE - 2)
    if _conf["score"] < _min_conf:
        logger.warning(
            "Entry BLOCKED by confluence gate: %s score=%d < %d (setup=%s, missing: %s)",
            _t, _conf["score"], _min_conf, _setup, ", ".join(_conf.get("missing") or []),
        )
        log_gate(_t, "confluence", True,
                 f"score {_conf['score']}/10 < {_min_conf}",
                 {"score": _conf["score"], "min": _min_conf,
                  "missing": _conf.get("missing") or []})
        return None
    entry["confluence_score"] = _conf["score"]
    entry["confluence_items"] = _conf["items"]

    # ---- Correlation gate ----
    _holdings = [
        tr.get("ticker") for tr in _pf_snapshot.get("open_trades", []) if tr.get("ticker")
    ]
    _holdings = [h for h in _holdings if h and h.upper() != _t]
    if _holdings:
        try:
            _returns = get_returns([_t] + _holdings, days=config.CORRELATION_LOOKBACK_DAYS)
            _corrs = compute_correlations(_returns, _t)
            _high = {h: c for h, c in _corrs.items() if c >= config.MAX_CORRELATION}
            if len(_high) > config.MAX_CORRELATED_HOLDINGS:
                logger.warning(
                    "Entry BLOCKED by correlation gate: %s vs %s (corrs %s)",
                    _t, list(_high.keys()), _high,
                )
                log_gate(_t, "correlation", True,
                         f"{len(_high)} corr ≥ {config.MAX_CORRELATION}",
                         {"high_corrs": _high})
                return None
            if _corrs:
                entry["correlations"] = _corrs
        except Exception as e:
            logger.warning("Correlation check failed for %s: %s", _t, e)

    # ---- Red-team critic (KILL or low confidence → block + cooldown) ----
    if config.RED_TEAM_ENABLED:
        _critique = run_red_team(entry, _snap_md, regime, model)
        if isinstance(_critique, dict):
            _verdict = (_critique.get("verdict") or "").upper()
            _conf_rt = _critique.get("confidence_thesis_holds")
            _reason_rt = _critique.get("reason") or ""
            _modes = _critique.get("top_failure_modes") or []
            _kill = (
                _verdict == "KILL"
                or (isinstance(_conf_rt, (int, float)) and _conf_rt < config.RED_TEAM_MIN_CONFIDENCE)
            )
            if _kill:
                logger.warning(
                    "Entry BLOCKED by red-team: %s verdict=%s conf=%s reason=%s",
                    _t, _verdict, _conf_rt, _reason_rt,
                )
                log_gate(_t, "red_team", True,
                         f"verdict={_verdict} conf={_conf_rt}",
                         {"verdict": _verdict, "confidence": _conf_rt,
                          "failure_modes": _modes, "reason": _reason_rt})
                record_entry_gate_cooldown(_t, "red_team",
                                           f"verdict={_verdict} conf={_conf_rt}")
                return None
            entry["red_team_review"] = {
                "verdict": _verdict,
                "confidence_thesis_holds": _conf_rt,
                "top_failure_modes": _modes,
                "reason": _reason_rt,
            }

    # ---- DD-soft scaling (modifier) ----
    _scale = dd_scaling_factor(_pf_snapshot)
    if _scale < 1.0:
        _orig_dd = float(entry.get("size_eur") or 0)
        if _orig_dd > 0:
            entry["size_eur"] = round(_orig_dd * _scale, 2)
            entry["dd_soft_scale"] = {"factor": _scale, "original_size_eur": _orig_dd}
            logger.warning(
                "DD-soft scaling: size €%.2f → €%.2f (factor=%.2f)",
                _orig_dd, entry["size_eur"], _scale,
            )

    # ---- Auto-split single TP at 1R ----
    if config.AUTO_SPLIT_SINGLE_TP_AT_1R:
        _tp = entry.get("take_profit")
        _e = float(entry.get("entry_price") or 0)
        _s = float(entry.get("stop_loss") or 0)
        _risk = _e - _s if (_e > _s > 0) else 0
        _tp_list = _tp if isinstance(_tp, list) else ([_tp] if _tp else [])
        if len(_tp_list) == 1 and _risk > 0:
            _tp1_new = round(_e + _risk, 2)
            _tp2 = float(_tp_list[0])
            if _tp1_new < _tp2:
                entry["take_profit"] = [_tp1_new, _tp2]
                entry["auto_split_tp"] = True
                logger.info(
                    "Auto-split TP for partial scale-out: %s TP1=%.2f (1R) + TP2=%.2f (orig)",
                    _t, _tp1_new, _tp2,
                )

    # ---- Whole-share hard gate (post all size-modifiers) ----
    _e = float(entry.get("entry_price") or 0)
    _size_ws = float(entry.get("size_eur") or 0)
    _whole = int(_size_ws / _e) if _e > 0 else 0
    if _whole < 1:
        _frac = (_size_ws / _e) if _e > 0 else 0
        logger.warning(
            "Entry BLOCKED by whole_shares: %s €%.2f / size €%.2f = %.3f Stk (<1, no SL on TR)",
            _t, _e, _size_ws, _frac,
        )
        log_gate(_t, "whole_shares", True,
                 f"price €{_e:.2f} > size €{_size_ws:.2f} (only {_frac:.3f} shares)",
                 {"price": _e, "size_eur": _size_ws, "whole_shares": round(_frac, 3)})
        return None

    # ---- Fixed-fee gate (TP1 gross ≥ 2× fee + min_net) ----
    _tp_fee = entry.get("take_profit")
    _tp1_fee = (
        float(_tp_fee[0]) if isinstance(_tp_fee, list) and _tp_fee
        else (float(_tp_fee) if isinstance(_tp_fee, (int, float)) else 0)
    )
    _shares_fee = int(_size_ws / _e) if _e > 0 else 0
    _gross_profit_eur = (_tp1_fee - _e) * _shares_fee if _tp1_fee > _e > 0 else 0
    _fees_roundtrip = 2 * config.FIXED_FEE_EUR_PER_SIDE
    _required_eur = _fees_roundtrip + config.MIN_NET_PROFIT_EUR
    if _gross_profit_eur < _required_eur:
        logger.warning(
            "Entry BLOCKED by fee_gate: %s gross @TP1 €%.2f < required €%.2f "
            "(fees €%.2f + min_net €%.2f); shares=%d, TP1=%.2f, entry=%.2f",
            _t, _gross_profit_eur, _required_eur, _fees_roundtrip,
            config.MIN_NET_PROFIT_EUR, _shares_fee, _tp1_fee, _e,
        )
        log_gate(_t, "fee_gate", True,
                 f"gross @TP1 €{_gross_profit_eur:.2f} < €{_required_eur:.2f}",
                 {"gross_profit_eur": round(_gross_profit_eur, 2),
                  "fees_roundtrip_eur": _fees_roundtrip,
                  "min_net_eur": config.MIN_NET_PROFIT_EUR,
                  "shares": _shares_fee, "tp1": _tp1_fee, "entry": _e})
        return None

    # ---- All gates passed → assemble rec + send Telegram alert ----
    log_gate(
        _t, "all_passed", False, "entry approved",
        {"size_eur": entry.get("size_eur"), "conviction": entry.get("conviction"),
         "p_win": entry.get("p_win")},
    )
    _vix_at_entry = (market_ctx.get("^VIX") or {}).get("price")
    _spy = market_ctx.get("SPY5.DE") or {}
    _spy_price = _spy.get("price")
    _spy_ma200 = _spy.get("ma200")
    _spy_above_ma200 = (
        bool(_spy_price > _spy_ma200)
        if isinstance(_spy_price, (int, float)) and isinstance(_spy_ma200, (int, float))
        else None
    )
    from core.portfolio import PROMPT_VERSION, STRATEGY_VERSION
    rec = {
        **entry,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending",
        "regime_at_entry": regime,
        "vix_at_entry": _vix_at_entry if isinstance(_vix_at_entry, (int, float)) else None,
        "spy_above_ma200": _spy_above_ma200,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "decision_version": entry.get("decision_version") or PROMPT_VERSION,
    }
    _ticker = rec.get("ticker", "?")
    _entry_v = rec.get("entry_price", 0)
    _sl_v = rec.get("stop_loss", 0)
    _tp_v = rec.get("take_profit", [])
    _size_v = rec.get("size_eur", 0)
    _conv = rec.get("conviction", 0)
    _hmin = rec.get("hold_days_min", "?")
    _hmax = rec.get("hold_days_max", "?")
    _thesis = rec.get("thesis", "")
    _trail = rec.get("trailing_stop_pct")
    _tp_str = " / ".join(f"€{t:.2f}" for t in (_tp_v if isinstance(_tp_v, list) else [_tp_v]))
    _trail_line = f"\nTrailing: {_trail}%" if _trail else ""
    _capital = float(
        _pf_snapshot.get("total_capital_eur", config.BUDGET_EUR) or config.BUDGET_EUR
    )
    _cash = float(_pf_snapshot.get("cash_eur", 0) or 0)
    _shares_raw = _size_v / _entry_v if _entry_v > 0 else 0.0
    if _shares_raw >= 1:
        _shares_prev = float(int(_shares_raw))
        _shares_str = f"{int(_shares_prev)} Stk"
    else:
        _shares_prev = round(_shares_raw, 2)
        _shares_str = f"{_shares_prev:.2f} Stk (Bruchstück)"
    _actual_size = round(_shares_prev * _entry_v, 2)
    _pct_cap = (_actual_size / _capital * 100) if _capital > 0 else 0.0
    _risk_eur = (_entry_v - _sl_v) * _shares_prev if _entry_v > _sl_v > 0 else 0.0
    _risk_pct = (_risk_eur / _capital * 100) if _capital > 0 else 0.0

    _rt = rec.get("red_team_review") or {}
    _rt_block = ""
    if _rt:
        _rt_modes = _rt.get("top_failure_modes") or []
        _rt_modes_str = "\n  • " + "\n  • ".join(_rt_modes[:3]) if _rt_modes else ""
        _rt_conf = _rt.get("confidence_thesis_holds")
        _rt_verdict = _rt.get("verdict") or "?"
        _rt_emoji = "🐻" if _rt_verdict == "WEAKEN" else "✅"
        _rt_block = (
            f"\n{_rt_emoji} *Red-Team*: {_rt_verdict} (conf {_rt_conf})"
            f"{_rt_modes_str}\n"
        )

    _wkn = config.TR_WKN_MAP.get(_ticker)
    _wkn_line = f"\n📱 TR-WKN: `{_wkn}` (yfinance: {_ticker})" if _wkn else ""

    message_id = _notify(
        f"🎯 *ENTRY* | `{_ticker}` | {_shares_str} à €{_entry_v:.2f} = €{_actual_size:.2f}\n"
        f"Grund: {_thesis}\n"
        f"Conv {_conv}/5\n"
        f"SL €{_sl_v:.2f} | TP {_tp_str} | Risk €{_risk_eur:.2f} ({_risk_pct:.2f}% Kap.) | "
        f"Hold {_hmin}-{_hmax}d | Cash €{_cash:.0f}{_trail_line}"
        f"{_wkn_line}"
        f"{_rt_block}\n"
        f"_Reply `/confirm` (auto={_shares_str}) oder `/confirm <stück> @<preis>` für override._"
    )
    if message_id:
        rec["message_id"] = message_id
    if MEMPALACE_AVAILABLE:
        log_trade(rec, "RECOMMENDED", rec.get("thesis", ""))
    logger.info("Entry recommendation: %s @ €%.2f (msg_id=%s)", _ticker, _entry_v, message_id)
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
