"""Claude-powered portfolio analysis orchestrator.

Composes market data + portfolio state + macro + prompts into a single
Claude call, handles tool_use responses (set_watch_levels / recommend_entry),
and persists results under the portfolio lock.
"""

import re
import json
import logging
from datetime import datetime

from anthropic import Anthropic
from dotenv import load_dotenv

import config
from notifier import send_notification as _notify, send_actionable
from memory import (
    log_trade, log_analysis, build_history_context, MEMPALACE_AVAILABLE,
)
from macro import today_events as _today_macro_events, format_events as _format_macro_events

from core.api_usage import can_make_api_call, increment_usage
from core.gate_log import log_gate
from core.call_log import log_claude_call
from core.prompts import (
    STRATEGY_SYSTEM, MORNING_PREP_PROMPT, OPENING_CHECK_PROMPT, EVENT_TRIGGER_PROMPT,
    WATCH_LEVELS_TOOL, RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
    UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL, SUBMIT_PASS_TOOL,
    RED_TEAM_SYSTEM, RED_TEAM_TOOL,
)
from core.portfolio import (
    portfolio_lock, load_portfolio, save_portfolio, suggest_position_size,
    compute_hit_stats, format_hit_stats,
    compute_portfolio_heat, format_portfolio_heat,
    compute_sector_exposure, format_sector_exposure,
    compute_equity_stats, format_equity_stats,
    risk_halt_status, maintain_drawdown_state, edge_ok,
    compute_confluence, format_confluence,
    compute_correlations, dd_scaling_factor,
    max_affordable_share_price_eur,
)
from core.market_data import (
    get_market_data, get_earnings_warnings, fetch_news, market_regime,
    get_returns,
)

load_dotenv()
logger = logging.getLogger(__name__)

client = Anthropic()


# ---------- Token-saving helpers ----------

def _compact(d):
    """Strip None and empty-string values recursively. Shrinks input tokens."""
    if isinstance(d, dict):
        return {k: _compact(v) for k, v in d.items() if v is not None and v != ""}
    if isinstance(d, list):
        return [_compact(x) for x in d]
    return d


def _dump(d) -> str:
    """Compact JSON: no indent, no spaces, None stripped, UTF-8 preserved."""
    return json.dumps(_compact(d), separators=(",", ":"), ensure_ascii=False)


# ---------- Thesis-degradation diff ----------

# Lower index = more bullish. analyst_rec_key downgrade = rank-increase ≥1.
_REC_KEY_RANK = {
    "strong_buy": 0, "buy": 1, "outperform": 1,
    "hold": 2, "neutral": 2,
    "underperform": 3, "sell": 4, "strong_sell": 4,
}


def _build_thesis_degradation_lines(open_trades: list, market_data: dict) -> list[str]:
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

def _run_red_team(rec: dict, snap: dict | None, regime: str, model: str) -> dict | None:
    """Bear-case critique of a proposed entry. Returns critique dict or None on failure.

    The critique runs as a single tool-forced Claude call. Cache key is the
    bear-critic system prompt (stable) — only the user-message changes per rec.
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
        f"## Empfehlung\n{_dump(payload)}\n\n"
        f"## Markt-Kontext für {payload['ticker']}\n{_dump(snap_slim or {})}\n\n"
        f"## Regime\n{regime}"
    )

    try:
        resp = client.messages.create(
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


# ---------- Trace builder (per-mode pipeline visibility) ----------

_TRACE_KEY_BY_MODE = {
    "morning": "last_morning_trace",
    "event": "last_event_trace",
    # opening uses event_context to pick xetra/us slot; resolved at call site.
}


def _build_trace(
    mode: str,
    response,
    *,
    watch_tool_called: bool,
    watch_tool_raw_input: dict | None,
    new_levels: list | None,
    text_parts: list[str],
    max_tokens: int,
) -> dict:
    """Capture pipeline state for /brain dashboard + Telegram /morning reply.

    Mirrors what we wish we'd had on 2026-04-29 when set_watch_levels({}) silently
    masked a max_tokens truncation as 'no setups today' for 3 days.
    """
    _stop = getattr(response, "stop_reason", None)
    _usage = getattr(response, "usage", None)
    _out_tok = getattr(_usage, "output_tokens", None) if _usage else None
    malformed = (
        watch_tool_called
        and watch_tool_raw_input is not None
        and "levels" not in watch_tool_raw_input
    )
    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "tool_called": watch_tool_called,
        "tool_input_keys": sorted(watch_tool_raw_input.keys()) if watch_tool_raw_input else [],
        "raw_levels_count": len(new_levels) if new_levels is not None else 0,
        "raw_tickers": [(lvl.get("ticker") or "?") for lvl in (new_levels or [])],
        "stop_reason": _stop,
        "output_tokens": _out_tok,
        "max_tokens_budget": max_tokens,
        "truncated": _stop == "max_tokens",
        "malformed_tool_input": malformed,
        "sonnet_text": ("\n".join(t for t in text_parts if t))[:400],
    }


def _log_trace_warnings(trace: dict) -> None:
    """Emit INFO summary + ERROR-level for the failure-modes that masked the
    2026-04-29 watchlevel outage (no tool call / malformed input / truncated)."""
    mode = trace.get("mode") or "?"
    logger.info(
        "TRACE [%s]: tool_called=%s raw_levels=%d stop=%s out_tok=%s/%s truncated=%s malformed=%s text_preview=%r",
        mode,
        trace["tool_called"],
        trace["raw_levels_count"],
        trace["stop_reason"],
        trace["output_tokens"],
        trace["max_tokens_budget"],
        trace["truncated"],
        trace["malformed_tool_input"],
        trace["sonnet_text"][:120],
    )
    # Morning is the only mode that *requires* set_watch_levels — others may legitimately
    # call no watch-tool (recommend_entry / recommend_exit / no action).
    if mode == "morning" and not trace["tool_called"]:
        logger.error("TRACE [%s]: set_watch_levels NOT called", mode)
    elif trace["malformed_tool_input"]:
        logger.error(
            "TRACE [%s]: tool input malformed (keys=%s) — likely truncated",
            mode, trace["tool_input_keys"],
        )
    elif trace["truncated"]:
        logger.error(
            "TRACE [%s]: response truncated at max_tokens=%d — bump budget or shorten prompt",
            mode, trace["max_tokens_budget"],
        )


def compute_correlation_snapshot(portfolio: dict) -> dict | None:
    """Pairwise return-correlation matrix over open positions for the dashboard.
    Returns None if <2 open trades or returns fetch fails. Used by morning
    analyzer AND /confirm path so the dashboard heatmap fills in immediately
    after opening a 2nd position (prev: stayed empty until next morning)."""
    _open = [t["ticker"] for t in portfolio.get("open_trades", []) if t.get("ticker")]
    if len(_open) < 2:
        return None
    try:
        _ret = get_returns(_open, days=config.CORRELATION_LOOKBACK_DAYS)
    except Exception:
        logger.exception("Correlation snapshot failed (returns fetch)")
        return None
    _m: dict[str, dict[str, float]] = {}
    for a in _open:
        sa = _ret.get(a)
        if sa is None:
            continue
        _m[a] = {}
        for b in _open:
            if a == b:
                _m[a][b] = 1.0
                continue
            sb = _ret.get(b)
            if sb is None:
                continue
            try:
                c = float(sa.corr(sb))
                if c == c:
                    _m[a][b] = round(c, 2)
            except Exception:
                continue
    return {
        "tickers": _open,
        "matrix": _m,
        "lookback_days": config.CORRELATION_LOOKBACK_DAYS,
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _trace_key(mode: str, event_context: str | None) -> str | None:
    """Pick portfolio.json key for the trace. Opening splits xetra/us via context."""
    if mode in _TRACE_KEY_BY_MODE:
        return _TRACE_KEY_BY_MODE[mode]
    if mode == "opening":
        ctx = (event_context or "").upper()
        if "XETRA" in ctx:
            return "last_opening_trace_xetra"
        if " US " in f" {ctx} " or ctx.startswith("US"):
            return "last_opening_trace_us"
        return "last_opening_trace"
    return None


def _auto_paper_open(rec: dict) -> None:
    """Mirror a passing entry-rec into the paper portfolio for parallel learning.

    Same shape as /confirm but auto, no slippage, €1 fee. Whole-share + fee gates
    already filtered by the time we get here, so int(size_eur/entry) >= 1 is implied;
    defensive guard added anyway. KEIN Telegram, KEIN MemPalace — Paper-Spur soll
    User nicht spammen und Real-History sauber halten.
    """
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


# ---------- Main orchestrator ----------

def analyze_portfolio(
    mode: str = "standard",
    event_context: str = None,
    force: bool = False,
    bypass_cooldown: bool = False,
) -> str:
    """Run portfolio analysis with Claude.

    Modes:
    - "morning":  Full daily briefing + watch-level planning (Sonnet)
    - "opening":  Lightweight gap-check at market open (Haiku)
    - "event":    Quick analysis triggered by a watch-level hit / price-alert
    - "standard": Regular analysis

    force: Bypass regular cooldown for high-priority situations.
    bypass_cooldown: Manual override (/morning) — skip even forced-cooldown.
    """
    is_high_priority = mode in ("morning", "opening", "event") or force
    allowed, reason = can_make_api_call(
        force=is_high_priority, bypass_cooldown=bypass_cooldown
    )
    if not allowed:
        return f"⚠️ Analysis skipped: {reason}"

    # DD-state hysteresis maintenance: latch/unlatch halt before any read.
    with portfolio_lock:
        _fresh = load_portfolio()
        if maintain_drawdown_state(_fresh):
            save_portfolio(_fresh)

    portfolio = load_portfolio()

    open_trade_tickers = [t["ticker"] for t in portfolio.get("open_trades", [])]
    watch_level_tickers = [w["ticker"] for w in portfolio.get("watch_levels", [])]
    excluded = set(config.EXCLUDED_TICKERS)
    tradeable = [
        t for t in set(open_trade_tickers + watch_level_tickers + config.WATCHLIST + config.COMMODITIES)
        if t not in excluded
    ]
    market_tickers = list(config.MARKET_INDICATORS)

    market_data = get_market_data(tradeable)
    market_ctx = get_market_data(market_tickers)

    # Liquidity pre-filter: drop illiquid/wide-spread tickers before Claude.
    # Keep open-trade + watch-level tickers always (exit decisions + breakout context
    # need them; watch levels were set for a reason — don't silently drop them on a
    # thin-volume day).
    # Whole-share-Filter (Preis-Cap): Tickers > MAX_POSITION_SIZE_PERCENT × total_capital
    # raus, weil TR-SL nur auf ganzen Stücken läuft. Protected-Set bleibt (offene Pos
    # für Exit + Alt-Watch-Level). Stage-2-Filter im set_watch_levels-Merge verhindert,
    # dass _protected sich neu mit teuren Tickers füllt.
    _max_share_price = max_affordable_share_price_eur(portfolio)
    _kept = {}
    _dropped_illiquid = []
    _dropped_unaffordable = []
    _protected = set(open_trade_tickers) | set(watch_level_tickers)
    # Opening: 5min volume vs daily avg = inherently tiny → looser gate to keep
    # early-XETRA tickers visible. Spread check still active (data-quality).
    _gate_vol = config.MIN_VOLUME_RATIO_OPENING if mode == "opening" else config.MIN_VOLUME_RATIO
    for _t, _d in market_data.items():
        if not isinstance(_d, dict) or _d.get("error"):
            _kept[_t] = _d
            continue
        if _t in _protected:
            _kept[_t] = _d
            continue
        vr = _d.get("volume_ratio")
        sp = _d.get("spread_pct")
        pr = _d.get("price")
        # vol_ratio==0.0 means yfinance returned no aggregated volume (XETRA-Open
        # before daily-bar settles, or stale cache after weekend). Treat as no-data
        # → keep ticker; gate only when vol_ratio is positive but below threshold.
        # Bug 2026-05-07: 14 tickers dropped with vol_ratio=0.0 at 09:35 → Haiku saw
        # near-empty market_data and emitted no recs.
        if isinstance(vr, (int, float)) and 0 < vr < _gate_vol:
            _dropped_illiquid.append(f"{_t}(vol_ratio={vr})")
            continue
        if sp is not None and sp > config.MAX_SPREAD_PERCENT:
            _dropped_illiquid.append(f"{_t}(spread={sp}%)")
            continue
        if isinstance(pr, (int, float)) and pr > _max_share_price:
            _dropped_unaffordable.append(f"{_t}(price=€{pr:.2f}>€{_max_share_price:.2f})")
            continue
        _kept[_t] = _d
    if _dropped_illiquid:
        logger.info("Liquidity gate dropped (mode=%s, vol_min=%.2f): %s",
                    mode, _gate_vol, ", ".join(_dropped_illiquid))
    if _dropped_unaffordable:
        logger.info("Whole-share gate dropped (cap=€%.2f): %s",
                    _max_share_price, ", ".join(_dropped_unaffordable))
    market_data = _kept

    regime = market_regime(market_ctx)
    cash = portfolio.get("cash_eur", config.BUDGET_EUR)
    atr_sizes = {
        t: suggest_position_size(market_data[t].get("atr14_pct"), cash)
        for t in tradeable if t in market_data and not market_data[t].get("error")
    }

    # --- Select prompt + tools by mode ---
    tools = None
    if mode == "morning":
        system_prompt = STRATEGY_SYSTEM + "\n\n" + MORNING_PREP_PROMPT
        system_prompt += (
            "\n\nWICHTIG: Rufe IMMER `set_watch_levels` auf (auch mit leerer Liste — empty=behält bestehende). "
            "Bei A+-Setup mit Conv ≥3/5: AUCH `recommend_entry` aufrufen. "
            "Bei verstärkter These bestehender Position: `recommend_add_to_position`. "
            "Pro offene Position: prüfe ob SL/TP noch passen → `update_position_targets` "
            "(z.B. neuer Resistance, Goldman-target raise). "
            "Bei Thesis-Bruch / Earnings-Defense: `recommend_exit`."
        )
        context_intro = "MORNING"
        tools = [
            WATCH_LEVELS_TOOL, RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
            UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL,
        ]
    elif mode == "opening":
        system_prompt = STRATEGY_SYSTEM + "\n\n" + OPENING_CHECK_PROMPT
        system_prompt += (
            "\n\nOUTPUT-FORMAT: AUSSCHLIESSLICH via tool_use, KEINE Prosa. Wenn keine "
            "Action passt → `submit_pass` mit 1-Satz-Reason. Spart Tokens + verhindert "
            "lange Texte ohne Decision."
        )
        context_intro = f"OPEN-CHECK {event_context or ''}".strip()
        tools = [
            RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
            UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL, SUBMIT_PASS_TOOL,
        ]
    elif mode == "event":
        system_prompt = STRATEGY_SYSTEM + "\n\n" + EVENT_TRIGGER_PROMPT
        system_prompt += (
            "\n\nBei ENTRY-Empfehlung mit Conv ≥3/5: `recommend_entry`. "
            "Bei verstärkter These offener Position: `recommend_add_to_position`. "
            "Bei SL/TP-Anpassung wegen Catalyst: `update_position_targets`. "
            "Bei Thesis-Bruch: `recommend_exit`. "
            "Wenn keine Action passt: `submit_pass` mit 1-Satz-Reason. "
            "OUTPUT: AUSSCHLIESSLICH via tool_use, KEINE Prosa (spart Tokens)."
        )
        context_intro = f"🚨 EVENT: {event_context}"
        # Watch-Levels sind Morning-Domain. Event-Mode darf sie nicht überschreiben
        # (Bug 2026-04-27: News-Event-Call rief set_watch_levels([]) und löschte 5 Morning-Levels).
        tools = [
            RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
            UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL, SUBMIT_PASS_TOOL,
        ]
    else:
        system_prompt = STRATEGY_SYSTEM
        context_intro = "Standard-Analyse"

    # --- MemPalace history context ---
    history_context = ""
    if MEMPALACE_AVAILABLE:
        primary_ticker = None
        if mode == "event" and event_context:
            m = re.search(r"\b([A-Z0-9]{1,6}(?:\.[A-Z]{1,3})?)\b", event_context)
            if m:
                primary_ticker = m.group(1)
        if not primary_ticker and open_trade_tickers:
            primary_ticker = open_trade_tickers[0]

        history_context = build_history_context(
            ticker=primary_ticker,
            query=event_context if mode == "event" else None,
        )

    # Always-on mistake summary: class distribution over last 20 losses.
    # Teaches Claude which failure modes dominate recent history.
    _closed = portfolio.get("closed_trades", [])
    _losses = [t for t in _closed if (t.get("pnl_pct") or 0) <= 0][-20:]
    mistake_summary = ""
    if _losses:
        by_class: dict[str, int] = {}
        by_tag: dict[str, int] = {}
        for t in _losses:
            cls = t.get("mistake_class") or "untagged"
            tag = t.get("mistake_tag") or "untagged"
            by_class[cls] = by_class.get(cls, 0) + 1
            by_tag[tag] = by_tag.get(tag, 0) + 1
        top_cls = sorted(by_class.items(), key=lambda x: -x[1])
        top_tag = sorted(by_tag.items(), key=lambda x: -x[1])[:5]
        mistake_summary = (
            f"Letzte {len(_losses)} Losses nach Klasse: "
            + ", ".join(f"{c}={n}" for c, n in top_cls)
            + " | Top-Tags: " + ", ".join(f"{t}={n}" for t, n in top_tag)
        )

    # --- Trim market_ctx to essentials (saves ~80% of those tokens) ---
    _market_ctx_slim: dict = {}
    for _t, _d in market_ctx.items():
        if not isinstance(_d, dict) or _d.get("error"):
            continue
        if _t == "SPY5.DE":
            _keep = ("price", "prev_close", "change_pct", "ma50", "ma200", "rsi14")
        elif _t == "EQQQ.DE":
            _keep = ("price", "change_pct", "rsi14")
        elif _t == "^VIX":
            _keep = ("price", "change_pct")
        else:
            _keep = ("price", "change_pct")
        _market_ctx_slim[_t] = {k: _d.get(k) for k in _keep if _d.get(k) is not None}

    # Format enforcer prepended for morning/opening — Claude ignores system-prompt
    # format rules otherwise and dumps multi-paragraph analyses into the text block.
    format_header = ""
    if mode == "morning":
        format_header = (
            "🚨 OUTPUT-REGEL (ZWINGEND): Dein Text-Output MUSS mit GENAU einer dieser Zeilen beginnen:\n"
            "  • `TICKER | €X (+/-X%) | SL/TP | HALTEN/CLOSE/...` (für offene Position)\n"
            "  • `TICKER | Entry €X | SL €X | TP €X | Size €X | Conv X/5 | These ...` (für A+ Setup)\n"
            "  • `Keine Setups heute.` (wenn kein A+ und keine offenen Positionen)\n"
            "KEINE Einleitung, KEIN **Header**, KEIN 'Internal Analysis', KEIN Reasoning-Text.\n"
            "Deine Analyse passiert intern + in Tool-Calls. Text-Output = nur die 3 erlaubten Zeilen-Formen.\n\n"
        )
    elif mode == "opening":
        format_header = (
            "🚨 OUTPUT-REGEL (ZWINGEND): Erste Zeile MUSS mit GENAU einem Prefix beginnen:\n"
            "  • `EXIT: TICKER | Grund [max 10 Worte]`\n"
            "  • `ENTRY: TICKER | Entry €X | SL €X | TP €X | Size €X | Conv X/5 | These ...` (+ recommend_entry Tool)\n"
            "  • `ADD: TICKER | Grund` (+ recommend_add_to_position Tool, nur bei offener Position)\n"
            "  • `PASS: TICKER | Grund` (kein Adjustment — wird gedroppt)\n"
            "Wenn nichts actionable: KEIN Output. KEIN Internal Analysis Block.\n\n"
        )
    elif mode == "event":
        format_header = (
            "🚨 OUTPUT-REGEL (ZWINGEND): Genau EINE Zeile, beginnend mit:\n"
            "  • `ENTRY: TICKER | Entry €X | SL €X | TP €X | Size €X | Conv X/5 | These ...` (+ recommend_entry Tool, Conv ≥3)\n"
            "  • `EXIT: TICKER @ €X | Grund`\n"
            "  • `ADD: TICKER | Grund` (+ recommend_add_to_position Tool, nur bei offener Position)\n"
            "  • `PASS: TICKER | Grund` (kein Edge — kurz warum)\n"
            "KEIN Internal Analysis Block, KEIN Reasoning-Text. User braucht Entscheidung, nicht Begründung.\n\n"
        )

    analysis_request = f"""{format_header}{context_intro}

Datum/Zeit: {datetime.now().strftime("%Y-%m-%d %H:%M")}

## Portfolio
Kapital: €{portfolio.get('total_capital_eur', config.BUDGET_EUR):.2f}
Cash: €{portfolio.get('cash_eur', config.BUDGET_EUR):.2f}

## Offene Positionen
{_dump(portfolio.get('open_trades', [])) if portfolio.get('open_trades') else "Keine"}

## Aktive Watch Levels
{_dump(portfolio.get('watch_levels', [])) if portfolio.get('watch_levels') else "Keine definiert"}

## Markt-Kontext (Indizes + VIX)
{_dump(_market_ctx_slim)}

## Live Kurse (mit RSI, MACD, MA20/50, BB, VWAP, Intraday-OHLC, Analyst-Konsens)
{_dump(market_data)}

## Markt-Regime
{regime}

## ATR-basierte Positionsgrößen (€{cash:.0f} Cash, {config.MAX_RISK_PER_TRADE_PERCENT}% Risiko/Trade)
{_dump(atr_sizes)}
"""

    # Thesis-degradation diff (event + opening + morning):
    # Compare each open trade's frozen entry_snapshot against current market_data.
    # Surface only DOWNGRADE / STRUCTURAL_BREAK signals so Claude has explicit
    # signal to consider exit instead of silently letting position rot.
    _degr_lines = _build_thesis_degradation_lines(
        portfolio.get("open_trades", []), market_data,
    )
    if _degr_lines:
        analysis_request += "\n\n## THESIS-STATUS (Snapshot vs. Jetzt)\n" + "\n".join(_degr_lines) + "\n"

    # Gap detection (morning + opening)
    if mode in ("morning", "opening"):
        gap_lines = []
        watched = set(open_trade_tickers) | {w["ticker"] for w in portfolio.get("watch_levels", [])}
        for t in watched:
            d = market_data.get(t, {})
            pct = d.get("change_pct")
            if pct is not None and abs(pct) >= config.GAP_FLAG_PERCENT:
                tag = "POS" if t in open_trade_tickers else "WATCH"
                price = d.get("price")
                price_str = f" @ €{price:.2f}" if price else ""
                gap_lines.append(f"  ⚠️ [{tag}] {t}: {pct:+.1f}%{price_str}")
        if gap_lines:
            analysis_request += (
                f"\n\n## GAPS (≥{config.GAP_FLAG_PERCENT}% vs prev close)\n"
                + "\n".join(gap_lines) + "\n"
            )

    if history_context:
        analysis_request += f"\n\n## Relevante History (aus MemPalace)\n{history_context}\n"

    if mistake_summary:
        analysis_request += f"\n\n## LAST-20 MISTAKES (Taxonomie)\n{mistake_summary}\n"

    # Portfolio heat (sizing-critical)
    if mode in ("morning", "opening"):
        heat = compute_portfolio_heat(portfolio)
        analysis_request += f"\n\n## PORTFOLIO HEAT\n{format_portfolio_heat(heat)}\n"

    # Sector exposure (cluster risk)
    if mode in ("morning", "opening"):
        sectors = compute_sector_exposure(portfolio)
        if sectors:
            analysis_request += f"\n\n## SECTOR EXPOSURE\n{format_sector_exposure(sectors)}\n"

    # Economic calendar (pre-release gating)
    if mode in ("morning", "opening"):
        macro = _today_macro_events()
        if macro:
            analysis_request += f"\n\n## ⚠️ HEUTE: HIGH-IMPACT EVENTS\n{_format_macro_events(macro)}\n"

    # Equity curve (morning only)
    if mode == "morning":
        eq = compute_equity_stats(
            portfolio.get("closed_trades", []),
            config.BUDGET_EUR,
            portfolio.get("cash_movements", []),
        )
        if eq:
            analysis_request += f"\n\n## EQUITY CURVE\n{format_equity_stats(eq)}\n"

    # Hit-rate self-calibration (morning only, gated at ≥3 trades)
    if mode == "morning":
        stats = compute_hit_stats(portfolio.get("closed_trades", []), portfolio.get("cash_movements", []))
        if stats:
            if stats.get("class_suggestion"):
                logger.warning("Self-calibration: %s", stats["class_suggestion"])
            analysis_request += f"\n\n## HIT-RATE (eigene History)\n{format_hit_stats(stats)}\n"
    # Brier-haircut visibility for event/opening too: Haiku must know its p_win
    # baseline gets adjusted before edge-gate so it doesn't blindly mirror
    # historical bias (Bug 2026-05-07: under-confident haircut=-0.38 silently
    # killed CON.DE +9% entry; surfacing the auto-correction lets Haiku set
    # p_win that already accounts for the bias direction).
    elif mode in ("event", "opening"):
        stats = compute_hit_stats(portfolio.get("closed_trades", []), portfolio.get("cash_movements", []))
        cal = (stats or {}).get("calibration") or {}
        if cal.get("haircut"):
            hc = cal["haircut"]
            direction = (
                f"Bot war historisch ZU PESSIMISTISCH (haircut={hc:+.2f}, edge gate "
                f"addiert {abs(min(0.20, abs(hc))):+.2f} auto auf dein p_win) — "
                f"sei AGGRESSIVER"
                if hc < 0 else
                f"Bot war historisch ZU OPTIMISTISCH (haircut={hc:+.2f}, edge gate "
                f"zieht {min(0.20, abs(hc)):.2f} auto von deinem p_win ab) — "
                f"sei STRENGER"
            )
            analysis_request += f"\n\n## BRIER-CAL\n{direction}\n"

    # Confluence scores per tradeable ticker (deterministic setup quality 0-10)
    if mode in ("morning", "opening", "event"):
        conf_lines = []
        for _t, _d in market_data.items():
            if not isinstance(_d, dict) or _d.get("error"):
                continue
            if _t in config.MARKET_INDICATORS or _t in config.COMMODITIES:
                continue
            _c = compute_confluence(_d, regime)
            if _c["score"] >= 4:  # only surface non-trivial scores
                hits = [k for k, v in _c["items"].items() if v]
                conf_lines.append(f"  {_t}: {_c['score']}/10 — {', '.join(hits)}")
        if conf_lines:
            analysis_request += (
                f"\n\n## CONFLUENCE-SCORES\n"
                + "\n".join(conf_lines) + "\n"
            )

    # Earnings calendar (morning only)
    if mode == "morning":
        earnings_tickers = [t["ticker"] for t in portfolio.get("open_trades", [])] + config.WATCHLIST + config.COMMODITIES
        ew = get_earnings_warnings(list(set(earnings_tickers)), days_ahead=3)
        if ew:
            ew_lines = "\n".join(
                f"  ⚠️ {w['ticker']}: Earnings in {w['days_until']} Tag(en) ({w['earnings_date']})"
                for w in ew
            )
            analysis_request += f"\n\n## Earnings Kalender (nächste 3 Tage)\n{ew_lines}\n"

    # News headlines (morning + event)
    if mode == "event":
        news_tickers = list({w["ticker"] for w in portfolio.get("watch_levels", [])})[:5]
    elif mode == "morning":
        open_t = [t["ticker"] for t in portfolio.get("open_trades", [])]
        news_tickers = list(dict.fromkeys(open_t + config.WATCHLIST))[:5]
    else:
        news_tickers = []

    if news_tickers:
        news = fetch_news(news_tickers, limit_per_ticker=3)
        if news:
            news_lines = "\n".join(
                f"  {t}: " + " | ".join(headlines)
                for t, headlines in news.items()
            )
            analysis_request += f"\n\n## Aktuelle News\n{news_lines}\n"

    # Output budgets. Morning needs room for set_watch_levels(3-7 levels)
    # serialized in tool_input — 400 truncated mid-tool-call (Bug 2026-04-29:
    # Sonnet output_tokens=400 → set_watch_levels({}) empty input → 0 levels).
    # Bug 2026-05-07: opening hit max_tokens=500/500 twice same day (XETRA + US),
    # both dropped recommend_entry for missing ['setup_type', 'top_fail_mode'].
    # recommend_entry's full schema serialized = ~600-800 output tokens.
    if mode == "morning":
        max_tokens = 1200
    elif mode == "opening":
        max_tokens = 900   # was 500 — truncated 2026-05-07 (PUM.DE recommend_entry x2)
    elif mode == "event":
        max_tokens = 900   # was 700 — recommend_entry tool-input + thesis text needs ≥800
    else:
        max_tokens = 400

    # Sonnet only for morning (senior reasoning). Opening/event use Haiku.
    model = config.CLAUDE_MODEL_MORNING if mode == "morning" else config.CLAUDE_MODEL_EVENT

    create_kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": analysis_request}],
    }
    if tools:
        create_kwargs["tools"] = tools

    # Tool-Only-Mode für event/opening: zwingt Tool-Use (keine Prosa-Antwort).
    # Spart Output-Tokens (~30-50% in event mode). submit_pass-Tool fängt "keine
    # Action"-Fall, damit Claude nicht zu einem Action-Tool gezwungen wird.
    # Morning bleibt freie Antwort, weil User den Brief liest.
    if tools and mode in ("event", "opening"):
        create_kwargs["tool_choice"] = {"type": "any"}

    # Hard-stop on known Claude intro-phrases that violate the tight-output format.
    # Bug 2026-05-07: morning hit stop_sequence at out=3 tokens — likely "**Setup-Screen"
    # or "**Watch Level Review" matched at the very start, killing the run before
    # set_watch_levels tool_use fired. Stop_sequences halt tool_use too, not just text.
    # Keeping only narrow prefix-style patterns; broad "**Header" patterns dropped.
    if mode in ("morning", "opening", "event"):
        create_kwargs["stop_sequences"] = [
            "Internal Analysis",
            "**Internal",
            "I'll analyze",
            "Analysiere die Daten",
            "Let me analyze",
            "Schritt 1:",
            "Step 1:",
        ]

    response = client.messages.create(**create_kwargs)
    increment_usage(forced=is_high_priority)

    def _log_usage(resp, turn: int = 1):
        usage = getattr(resp, "usage", None)
        if usage is not None:
            logger.info(
                "Claude call: mode=%s turn=%d in=%s out=%s cache_read=%s cache_write=%s",
                mode,
                turn,
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                getattr(usage, "cache_read_input_tokens", None),
                getattr(usage, "cache_creation_input_tokens", None),
            )

    _log_usage(response, turn=1)

    # --- Extract text + tool_use ---
    text_parts = []
    new_levels = None
    entry_recommendation = None
    add_recommendation = None
    update_targets = None
    exit_recommendation = None
    pass_reason: str | None = None
    tool_use_blocks = []

    watch_tool_called = False
    watch_tool_raw_input: dict | None = None
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            name = getattr(block, "name", None)
            if name == "set_watch_levels":
                watch_tool_called = True
                watch_tool_raw_input = block.input or {}
                new_levels = watch_tool_raw_input.get("levels", [])
            elif name == "recommend_entry":
                entry_recommendation = block.input or {}
            elif name == "recommend_add_to_position":
                add_recommendation = block.input or {}
            elif name == "update_position_targets":
                update_targets = block.input or {}
            elif name == "recommend_exit":
                exit_recommendation = block.input or {}
            elif name == "submit_pass":
                pass_reason = (block.input or {}).get("reason", "")
            tool_use_blocks.append(block)

    # Persistent log for /brain inspection page (one entry per call, swallows errors)
    log_claude_call(
        mode=mode,
        model=model,
        turn=1,
        system_prompt=system_prompt,
        user_message=analysis_request,
        text_response="\n".join(t for t in text_parts if t),
        tool_calls=[
            {"name": tb.name, "input": tb.input or {}}
            for tb in tool_use_blocks
        ],
        usage=getattr(response, "usage", None),
        extra={"event_context": event_context} if event_context else None,
    )

    # 2-Turn-Flow: tool calls without text → send tool_result back for summary.
    # Skip in event/opening (tool-only modes): wir wollen explizit KEINE Prosa-
    # Zusammenfassung, sonst frisst der 2. Turn die gesparten Tokens wieder auf.
    if (
        tool_use_blocks
        and response.stop_reason == "tool_use"
        and not text_parts
        and mode not in ("event", "opening")
    ):
        tool_result_content = []
        for tb in tool_use_blocks:
            if tb.name == "set_watch_levels":
                result_text = f"Watch Levels registriert: {len(new_levels or [])} Level(s)."
            elif tb.name == "recommend_entry":
                rec_ticker = (entry_recommendation or {}).get("ticker", "?")
                result_text = f"Entry-Empfehlung für {rec_ticker} gespeichert."
            elif tb.name == "recommend_add_to_position":
                add_ticker = (add_recommendation or {}).get("ticker", "?")
                result_text = f"Add-Empfehlung für {add_ticker} gespeichert."
            elif tb.name == "update_position_targets":
                upd_ticker = (update_targets or {}).get("ticker", "?")
                result_text = f"SL/TP-Update für {upd_ticker} gespeichert."
            elif tb.name == "recommend_exit":
                exit_ticker = (exit_recommendation or {}).get("ticker", "?")
                result_text = f"Exit-Empfehlung für {exit_ticker} gespeichert."
            elif tb.name == "submit_pass":
                result_text = f"PASS akzeptiert: {pass_reason or '(no reason)'}"
            else:
                result_text = "OK"
            tool_result_content.append({
                "type": "tool_result",
                "tool_use_id": tb.id,
                "content": result_text,
            })
        tool_result_content[-1]["content"] += " Kurze Zusammenfassung bitte."

        turn2_kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "system": create_kwargs["system"],
            "messages": [
                {"role": "user", "content": analysis_request},
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_result_content},
            ],
        }

        response2 = client.messages.create(**turn2_kwargs)
        increment_usage(forced=is_high_priority)
        _log_usage(response2, turn=2)

        _t2_text_parts: list[str] = []
        for block in response2.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
                _t2_text_parts.append(block.text)
        log_claude_call(
            mode=mode,
            model=model,
            turn=2,
            system_prompt=system_prompt,
            user_message="(turn-2 follow-up: tool_results + 'Kurze Zusammenfassung bitte.')",
            text_response="\n".join(_t2_text_parts),
            tool_calls=[],
            usage=getattr(response2, "usage", None),
        )

    # Dedupe consecutive identical lines: Sonnet sometimes emits the same
    # status line in multiple text-blocks (e.g. RWE.DE | … HALTEN twice when
    # tool_use is sandwiched between). User-visible noise.
    _raw_text = "\n".join(t for t in text_parts if t).strip()
    _dedup: list[str] = []
    for _ln in _raw_text.split("\n"):
        if _dedup and _ln.strip() == _dedup[-1].strip():
            continue
        _dedup.append(_ln)
    # Tool-Only-Modus: submit_pass.reason wird als analysis_text verwendet
    # (statt "(keine Text-Analyse)"), damit /brain-Inspector + Logs lesbar bleiben.
    if not _dedup and pass_reason:
        analysis_text = f"PASS: {pass_reason}"
    else:
        analysis_text = "\n".join(_dedup) or "(keine Text-Analyse)"

    # Build the rec BEFORE notification so we can attach the Telegram message_id.
    rec = None
    message_id: int | None = None

    if entry_recommendation:
        # Required-fields validation — Anthropic SDK accepts truncated tool_use
        # blocks where the JSON cuts off mid-stream and required keys silently
        # drop to None. Without these we can't bucket the trade in setup-type
        # hit-rate, can't run pre-mortem-vs-actual mistake-class validation,
        # and the trade ends up in the 'untagged' bucket forever.
        # (Bug 2026-04-30: SIE.DE + 3OIL.MI recs hit max_tokens=300, both
        # missing setup_type + top_fail_mode, persisted as untagged.)
        _required = ("ticker", "entry_price", "stop_loss", "take_profit",
                     "size_eur", "conviction", "p_win", "thesis",
                     "setup_type", "top_fail_mode")
        _missing = [k for k in _required if not entry_recommendation.get(k)]
        if _missing:
            log_gate(
                (entry_recommendation.get("ticker") or "?").upper(),
                "incomplete_rec", True,
                f"recommend_entry missing required fields: {','.join(_missing)} — "
                f"likely max_tokens truncation",
                {"missing": _missing, "received_keys": sorted(entry_recommendation.keys())},
            )
            logger.error(
                "recommend_entry DROPPED: %s missing %s (truncated tool_use?)",
                entry_recommendation.get("ticker"), _missing,
            )
            entry_recommendation = None

    if entry_recommendation:
        # Already-open silent drop: Claude got confused and called recommend_entry on
        # a ticker we already hold. Re-entry doubles position risk and can't pass the
        # /confirm flow cleanly. Pyramiding goes through recommend_add_to_position.
        # (Bug 2026-04-27: RWE.DE re-entry rec triggered news+entry+blocked triple-msg.)
        _t = (entry_recommendation.get("ticker") or "").upper()
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
            entry_recommendation = None

    if entry_recommendation and mode == "event":
        # Sonnet→Haiku-Pattern: Event-Mode (Haiku) darf nur entries empfehlen, deren
        # Ticker bereits ein aktives Morning-Watch-Level hat. Sonnet baut die Thesen,
        # Haiku verifiziert nur deterministische Conditions. Verhindert Mid-Day-
        # Improvisations-Entries auf 15min-verzögerten Daten.
        _t = (entry_recommendation.get("ticker") or "").upper()
        _watch_match = next(
            (w for w in load_portfolio().get("watch_levels", [])
             if (w.get("ticker") or "").upper() == _t),
            None,
        )
        if not _watch_match:
            logger.warning(
                "Entry BLOCKED by event-watch-coupling: %s has no morning watch_level",
                _t,
            )
            log_gate(
                _t, "event_watch_coupling", True,
                "no morning watch_level for event-mode entry",
                {"ticker": _t},
            )
            entry_recommendation = None
        else:
            # Stamp the morning thesis on the rec so downstream (telegram alert,
            # /confirm-snapshot) carries the Sonnet-vetted thesis, not the Haiku one.
            morning_thesis = _watch_match.get("thesis")
            if morning_thesis:
                entry_recommendation["watch_thesis"] = morning_thesis

    if entry_recommendation:
        # --- Risk gates: halt + edge ---
        _pf_snapshot = load_portfolio()
        _halt = risk_halt_status(_pf_snapshot)
        if _halt["halt"]:
            reason = " | ".join(_halt["reasons"])
            logger.warning("Entry BLOCKED by risk halt: %s", reason)
            log_gate(entry_recommendation.get("ticker", "?"), "risk_halt", True, reason, _halt.get("metrics"))
            if "risk_halt" in config.GATE_BLOCK_NOTIFY_WHITELIST:
                _notify(f"⛔ *ENTRY BLOCKIERT* ({entry_recommendation.get('ticker','?')})\n{reason}")
            entry_recommendation = None

    if entry_recommendation and config.RISK_OFF_BLOCKS_LONGS and regime.startswith("RISK_OFF"):
        direction = str(entry_recommendation.get("direction") or "LONG").upper()
        if direction == "LONG":
            logger.warning("Entry BLOCKED by regime gate: RISK_OFF + LONG")
            log_gate(
                entry_recommendation.get("ticker", "?"), "regime", True,
                f"RISK_OFF + LONG (regime={regime})", {"regime": regime},
            )
            entry_recommendation = None

    if entry_recommendation:
        # No-entry-zone: block new entries during open/close noise windows.
        # Why: auction spikes + EOD chop = bad fills + lag-amplified slippage on
        # 15min-delayed data. Watch-levels and SL/TP loop run unaffected.
        _now = datetime.now()
        _now_min = _now.hour * 60 + _now.minute
        _blocked_window = None
        for sh, sm, eh, em in config.NO_ENTRY_WINDOWS:
            if sh * 60 + sm <= _now_min < eh * 60 + em:
                _blocked_window = f"{sh:02d}:{sm:02d}–{eh:02d}:{em:02d}"
                break
        if _blocked_window:
            logger.warning(
                "Entry BLOCKED by no-entry-zone: %s in window %s",
                entry_recommendation.get("ticker", "?"), _blocked_window,
            )
            log_gate(
                entry_recommendation.get("ticker", "?"), "no_entry_zone", True,
                f"window {_blocked_window}", {"window": _blocked_window},
            )
            entry_recommendation = None

    if entry_recommendation:
        # SL-distance sanity: block if entry-SL is too tight or too wide vs. ATR.
        # Why: tight SL (<0.8×ATR) = guaranteed whipsaw; wide SL (>3×ATR) inflates
        # edge_ok's reward/risk math and breaks risk sizing. Runs before edge gate.
        _t = (entry_recommendation.get("ticker") or "").upper()
        _entry = float(entry_recommendation.get("entry_price") or 0)
        _sl = float(entry_recommendation.get("stop_loss") or 0)
        _atr = (market_data.get(_t) or {}).get("atr14")
        if _entry > _sl > 0 and isinstance(_atr, (int, float)) and _atr > 0:
            _sl_dist_atr = (_entry - _sl) / _atr
            if _sl_dist_atr < config.MIN_SL_DISTANCE_ATR:
                logger.warning(
                    "Entry BLOCKED by SL-too-tight: %s SL %.2f×ATR < %.2f×ATR",
                    _t, _sl_dist_atr, config.MIN_SL_DISTANCE_ATR,
                )
                log_gate(_t, "sl_distance", True,
                         f"SL {_sl_dist_atr:.2f}×ATR < {config.MIN_SL_DISTANCE_ATR}",
                         {"sl_dist_atr": round(_sl_dist_atr, 2), "kind": "tight"})
                entry_recommendation = None
            elif _sl_dist_atr > config.MAX_SL_DISTANCE_ATR:
                logger.warning(
                    "Entry BLOCKED by SL-too-wide: %s SL %.2f×ATR > %.2f×ATR",
                    _t, _sl_dist_atr, config.MAX_SL_DISTANCE_ATR,
                )
                log_gate(_t, "sl_distance", True,
                         f"SL {_sl_dist_atr:.2f}×ATR > {config.MAX_SL_DISTANCE_ATR}",
                         {"sl_dist_atr": round(_sl_dist_atr, 2), "kind": "wide"})
                entry_recommendation = None

    if entry_recommendation:
        # Brier-Haircut: shift p_win toward realized win-rate before edge gate.
        # haircut = avg_p_pred - actual_win_rate (positive = overconfident, subtract;
        # negative = underconfident, add). Bidirectional so Claude's well-calibrated
        # but pessimistic theses don't get permanently filtered (Bug 2026-05-07:
        # CON.DE +9% blocked by edge=-0.005 because haircut=-0.38 was ignored,
        # gate saw p_adj=p_raw=0.58 instead of 0.78).
        # Cap correction at ±0.20 to prevent runaway over-/under-confidence overrides
        # from small samples (haircut from <20 trades has high variance).
        _HAIRCUT_CAP = 0.20
        _p_raw = entry_recommendation.get("p_win")
        _stats = compute_hit_stats(_pf_snapshot.get("closed_trades", []), _pf_snapshot.get("cash_movements", []))
        _haircut = 0.0
        if _stats and _stats.get("calibration"):
            _haircut = _stats["calibration"].get("haircut") or 0.0
        if isinstance(_p_raw, (int, float)) and _haircut != 0:
            _capped_haircut = max(-_HAIRCUT_CAP, min(_HAIRCUT_CAP, _haircut))
            _p_adj = max(0.01, min(0.99, _p_raw - _capped_haircut))
        else:
            _p_adj = _p_raw

        _ok, _edge = edge_ok(
            _p_adj,
            entry_recommendation.get("entry_price"),
            entry_recommendation.get("stop_loss"),
            entry_recommendation.get("take_profit"),
        )
        if not _ok:
            logger.warning(
                "Entry BLOCKED by edge gate: edge=%.3f < %.3f (p_raw=%s, haircut=%s, p_adj=%s)",
                _edge, config.MIN_EXPECTED_EDGE, _p_raw, _haircut, _p_adj,
            )
            log_gate(
                entry_recommendation.get("ticker", "?"), "edge", True,
                f"edge {_edge:.3f} < {config.MIN_EXPECTED_EDGE}",
                {"edge": round(_edge, 3), "p_raw": _p_raw, "p_adj": _p_adj, "haircut": _haircut},
            )
            entry_recommendation = None

    if entry_recommendation:
        # Sector cluster gate: block if ticker's sector already at cap.
        # Why: 3× Semis long at once = one chip-crash hits three SLs. Diversification
        # is the only free lunch. 'other' (unmapped) is not enforced.
        _t = (entry_recommendation.get("ticker") or "").upper()
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
                entry_recommendation = None

    if entry_recommendation:
        # Adaptive-Kelly clamp: enforce max position-size from Brier-derived kelly_mult.
        # Why: when calibration is poor (high Brier), Claude's size proposal can over-bet
        # on a noisy edge estimate. Clamp size_eur to fractional-Kelly using adaptive mult.
        _p_clamp = entry_recommendation.get("p_win")
        _entry_c = float(entry_recommendation.get("entry_price") or 0)
        _sl_c = float(entry_recommendation.get("stop_loss") or 0)
        _tp_c = entry_recommendation.get("take_profit")
        _tp1 = _tp_c[0] if isinstance(_tp_c, list) and _tp_c else (_tp_c if isinstance(_tp_c, (int, float)) else None)
        if (
            isinstance(_p_clamp, (int, float)) and 0 < _p_clamp < 1
            and _entry_c > _sl_c > 0 and isinstance(_tp1, (int, float)) and _tp1 > _entry_c
        ):
            _r2r = (_tp1 - _entry_c) / (_entry_c - _sl_c)
            _km = (_stats or {}).get("kelly_mult") or config.KELLY_FRACTION
            _atr_pct = (market_data.get((entry_recommendation.get("ticker") or "").upper()) or {}).get("atr14_pct")
            _kelly_cap = suggest_position_size(
                _atr_pct, cash, p_win=_p_clamp, reward_to_risk=_r2r, kelly_mult=_km,
            )
            _orig_size = float(entry_recommendation.get("size_eur") or 0)
            if _orig_size > _kelly_cap > 0:
                entry_recommendation["size_eur"] = _kelly_cap
                entry_recommendation["kelly_clamp"] = {
                    "kelly_mult": _km, "r2r": round(_r2r, 2),
                    "original_size_eur": _orig_size, "capped_size_eur": _kelly_cap,
                }
                logger.warning(
                    "Kelly-clamp: size €%.2f → €%.2f (kelly_mult=%.2f, p=%.2f, R:R=%.2f)",
                    _orig_size, _kelly_cap, _km, _p_clamp, _r2r,
                )

    if entry_recommendation:
        # VIX size-dampening: shrink size under elevated/extreme volatility.
        # Why: higher realized range = wider stops + more gap risk. Same 3% risk/trade
        # but smaller notional so a fast move doesn't blow past SL intraday.
        _vix = (market_ctx.get("^VIX") or {}).get("price")
        _vix_factor = 1.0
        if isinstance(_vix, (int, float)):
            if _vix > 30:
                _vix_factor = 0.25
            elif _vix > 20:
                _vix_factor = 0.5
        if _vix_factor < 1.0:
            _orig = float(entry_recommendation.get("size_eur") or 0)
            if _orig > 0:
                entry_recommendation["size_eur"] = round(_orig * _vix_factor, 2)
                entry_recommendation["vix_dampener"] = {
                    "vix": _vix, "factor": _vix_factor, "original_size_eur": _orig,
                }
                logger.warning(
                    "VIX-dampener: size €%.2f → €%.2f (VIX=%.2f, factor=%.2f)",
                    _orig, entry_recommendation["size_eur"], _vix, _vix_factor,
                )

    if entry_recommendation:
        # Weekly-trend gate: no LONG against weekly downtrend.
        # Why: intraday entries against the weekly primary trend are low-hit-rate
        # mean reverts. CLAUDE.md rule, now enforced instead of advisory.
        _direction = str(entry_recommendation.get("direction") or "LONG").upper()
        _t = (entry_recommendation.get("ticker") or "").upper()
        _wk = (market_data.get(_t) or {}).get("wk_trend")
        if _direction == "LONG" and _wk == "DOWN":
            logger.warning("Entry BLOCKED by weekly-trend gate: %s LONG vs wk_trend=DOWN", _t)
            log_gate(_t, "weekly_trend", True, "LONG vs wk_trend=DOWN", {"wk_trend": _wk})
            entry_recommendation = None

    if entry_recommendation:
        # Earnings hard-block: T-N bis T+0 (Earnings-Day). Gap-Risiko ist Coin-Flip,
        # kein systematischer Edge. Override: Setup-Type=earnings_drift (Post-Earnings-Drift T+1+).
        _t = (entry_recommendation.get("ticker") or "").upper()
        _setup = (entry_recommendation.get("setup_type") or "").lower()
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
                entry_recommendation = None

    if entry_recommendation:
        # Relative-Strength Gate: kein LONG auf Lagger im Aufwärtstrend.
        # Override für mean_reversion / reversal_oversold / gap_fill (RS-negativ ist These dort).
        _t = (entry_recommendation.get("ticker") or "").upper()
        _setup = (entry_recommendation.get("setup_type") or "").lower()
        _rs = (market_data.get(_t) or {}).get("rs_20d_vs_index_pct")
        _rs_override_setups = {"mean_reversion", "reversal_oversold", "gap_fill"}
        if isinstance(_rs, (int, float)) and _setup not in _rs_override_setups:
            if _rs < config.MIN_RS_20D_VS_INDEX_PCT:
                logger.warning(
                    "Entry BLOCKED by RS gate: %s rs_20d=%+.2fpp < %.2fpp (setup=%s)",
                    _t, _rs, config.MIN_RS_20D_VS_INDEX_PCT, _setup,
                )
                log_gate(_t, "relative_strength", True,
                         f"rs_20d {_rs:+.1f}pp < {config.MIN_RS_20D_VS_INDEX_PCT}pp",
                         {"rs_20d": _rs, "setup": _setup})
                entry_recommendation = None

    if entry_recommendation:
        # Volume-Confirmation für Breakouts: Fake-Breakouts vermeiden.
        _t = (entry_recommendation.get("ticker") or "").upper()
        _setup = (entry_recommendation.get("setup_type") or "").lower()
        if _setup == "breakout_resistance":
            _vr = (market_data.get(_t) or {}).get("volume_ratio")
            if isinstance(_vr, (int, float)) and _vr < config.MIN_BREAKOUT_VOLUME_RATIO:
                logger.warning(
                    "Entry BLOCKED by volume gate: %s breakout vol_ratio=%.2f < %.2f",
                    _t, _vr, config.MIN_BREAKOUT_VOLUME_RATIO,
                )
                log_gate(_t, "breakout_volume", True,
                         f"vol_ratio {_vr:.2f} < {config.MIN_BREAKOUT_VOLUME_RATIO}",
                         {"vol_ratio": _vr})
                entry_recommendation = None

    if entry_recommendation:
        # Confluence-Score Gate: deterministisches Setup-Quality. Score < MIN → PASS.
        # Mean-reversion setups bekommen niedrigeren Threshold (Antithese zur Trend-Confluence).
        _t = (entry_recommendation.get("ticker") or "").upper()
        _setup = (entry_recommendation.get("setup_type") or "").lower()
        _snap = market_data.get(_t)
        _conf = compute_confluence(_snap, regime) if _snap else {"score": 0, "items": {}, "missing": ["no_data"]}
        _min_conf = config.MIN_CONFLUENCE_SCORE
        if _setup in ("mean_reversion", "reversal_oversold", "gap_fill"):
            _min_conf = max(3, config.MIN_CONFLUENCE_SCORE - 2)
        if _conf["score"] < _min_conf:
            logger.warning(
                "Entry BLOCKED by confluence gate: %s score=%d < %d (setup=%s, missing: %s)",
                _t, _conf["score"], _min_conf, _setup, ", ".join(_conf.get("missing") or []),
            )
            log_gate(_t, "confluence", True,
                     f"score {_conf['score']}/10 < {_min_conf}",
                     {"score": _conf["score"], "min": _min_conf, "missing": _conf.get("missing") or []})
            entry_recommendation = None
        else:
            entry_recommendation["confluence_score"] = _conf["score"]
            entry_recommendation["confluence_items"] = _conf["items"]

    if entry_recommendation:
        # Korrelations-Gate: cluster-risk auch quer durch Sektoren.
        # Wenn ≥MAX_CORRELATED_HOLDINGS bestehende Positionen Korrelation ≥MAX_CORRELATION → Block.
        _t = (entry_recommendation.get("ticker") or "").upper()
        _holdings = [tr.get("ticker") for tr in _pf_snapshot.get("open_trades", []) if tr.get("ticker")]
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
                    entry_recommendation = None
                elif _corrs:
                    entry_recommendation["correlations"] = _corrs
            except Exception as e:
                logger.warning("Correlation check failed for %s: %s", _t, e)

    if entry_recommendation and config.RED_TEAM_ENABLED:
        # Red-Team-Pass: Bear-Critic-Persona reviewt fertige Bull-Rec.
        # Block bei verdict==KILL ODER confidence < RED_TEAM_MIN_CONFIDENCE.
        # WEAKEN bleibt durch, wird nur am Telegram-Alert angeflanscht.
        _t = (entry_recommendation.get("ticker") or "").upper()
        _snap = market_data.get(_t)
        _critique = _run_red_team(entry_recommendation, _snap, regime, model)
        if isinstance(_critique, dict):
            _verdict = (_critique.get("verdict") or "").upper()
            _conf = _critique.get("confidence_thesis_holds")
            _reason = _critique.get("reason") or ""
            _modes = _critique.get("top_failure_modes") or []
            _kill = (
                _verdict == "KILL"
                or (isinstance(_conf, (int, float)) and _conf < config.RED_TEAM_MIN_CONFIDENCE)
            )
            if _kill:
                logger.warning(
                    "Entry BLOCKED by red-team: %s verdict=%s conf=%s reason=%s",
                    _t, _verdict, _conf, _reason,
                )
                log_gate(_t, "red_team", True,
                         f"verdict={_verdict} conf={_conf}",
                         {"verdict": _verdict, "confidence": _conf,
                          "failure_modes": _modes, "reason": _reason})
                # Silent block: user has no action on a killed entry — only logs +
                # dashboard (gate_log) keep the trail. Past Telegram alert was pure noise.
                entry_recommendation = None
            else:
                # Stamp critique on rec — surfaces in Telegram alert + dashboard.
                entry_recommendation["red_team_review"] = {
                    "verdict": _verdict,
                    "confidence_thesis_holds": _conf,
                    "top_failure_modes": _modes,
                    "reason": _reason,
                }

    if entry_recommendation:
        # Drawdown-Soft-Scaling: zwischen DD_SOFT und DD_HALT halbe Size.
        # Behavioral edge: kein Revenge-Trade nach Drawdown.
        _scale = dd_scaling_factor(_pf_snapshot)
        if _scale < 1.0:
            _orig = float(entry_recommendation.get("size_eur") or 0)
            if _orig > 0:
                entry_recommendation["size_eur"] = round(_orig * _scale, 2)
                entry_recommendation["dd_soft_scale"] = {
                    "factor": _scale, "original_size_eur": _orig,
                }
                logger.warning(
                    "DD-soft scaling: size €%.2f → €%.2f (factor=%.2f)",
                    _orig, entry_recommendation["size_eur"], _scale,
                )

    if entry_recommendation:
        # Auto-split single TP: wenn nur ein TP → TP1 bei 1R einfügen für Partial-Scale-Out.
        # 1R = entry + (entry-stop). Ermöglicht 50%-Partial @ TP1 + Runner zu TP2 (Original).
        if config.AUTO_SPLIT_SINGLE_TP_AT_1R:
            _tp = entry_recommendation.get("take_profit")
            _entry = float(entry_recommendation.get("entry_price") or 0)
            _sl = float(entry_recommendation.get("stop_loss") or 0)
            _risk = _entry - _sl if (_entry > _sl > 0) else 0
            _tp_list = _tp if isinstance(_tp, list) else ([_tp] if _tp else [])
            if len(_tp_list) == 1 and _risk > 0:
                _tp1 = round(_entry + _risk, 2)
                _tp2 = float(_tp_list[0])
                if _tp1 < _tp2:
                    entry_recommendation["take_profit"] = [_tp1, _tp2]
                    entry_recommendation["auto_split_tp"] = True
                    logger.info(
                        "Auto-split TP for partial scale-out: %s TP1=%.2f (1R) + TP2=%.2f (orig)",
                        entry_recommendation.get("ticker"), _tp1, _tp2,
                    )

    if entry_recommendation:
        # Whole-share Hard-Gate: nach allen Size-Modifikatoren (Kelly/VIX/DD-soft) muss
        # ≥1 ganzes Stück innerhalb size_eur passen, sonst ist auf TR keine SL-Order
        # platzierbar → Verstoß gegen Full-Trust-SL-Invariant. Pre-Filter im Liquidity-
        # Block fängt die meisten Fälle, dieser Gate fängt Edge-Cases (Ticker durch
        # _protected durchgelassen, oder size_eur durch Modifikatoren unter Aktienpreis
        # geschrumpft).
        _t = (entry_recommendation.get("ticker") or "?").upper()
        _entry = float(entry_recommendation.get("entry_price") or 0)
        _size = float(entry_recommendation.get("size_eur") or 0)
        _whole = int(_size / _entry) if _entry > 0 else 0
        if _whole < 1:
            _frac = (_size / _entry) if _entry > 0 else 0
            logger.warning(
                "Entry BLOCKED by whole_shares: %s €%.2f / size €%.2f = %.3f Stk (<1, no SL on TR)",
                _t, _entry, _size, _frac,
            )
            log_gate(_t, "whole_shares", True,
                     f"price €{_entry:.2f} > size €{_size:.2f} (only {_frac:.3f} shares)",
                     {"price": _entry, "size_eur": _size, "whole_shares": round(_frac, 3)})
            entry_recommendation = None

    if entry_recommendation:
        # Fixed-Fee-Gate: TR €1/Seite × 2 = €2 Roundtrip frisst kleine Trades.
        # Brutto-Gewinn auf whole-shares bei TP1 muss ≥ Fees + MIN_NET_PROFIT_EUR sein,
        # sonst Trade nach Kosten Null-Summe oder negativ. Conservative: TP1 statt TP2,
        # weil Auto-Split-Partial bei TP1 die Hälfte schließt → Fee fällt zweimal an
        # (Buy + Partial-Sell), Brutto-Gewinn aber nur halb. Gleichung gilt 1:1.
        _t = (entry_recommendation.get("ticker") or "?").upper()
        _entry = float(entry_recommendation.get("entry_price") or 0)
        _size = float(entry_recommendation.get("size_eur") or 0)
        _tp = entry_recommendation.get("take_profit")
        _tp1 = (
            float(_tp[0]) if isinstance(_tp, list) and _tp
            else (float(_tp) if isinstance(_tp, (int, float)) else 0)
        )
        _shares = int(_size / _entry) if _entry > 0 else 0
        _gross_profit_eur = (_tp1 - _entry) * _shares if _tp1 > _entry > 0 else 0
        _fees_roundtrip = 2 * config.FIXED_FEE_EUR_PER_SIDE
        _required = _fees_roundtrip + config.MIN_NET_PROFIT_EUR
        if _gross_profit_eur < _required:
            logger.warning(
                "Entry BLOCKED by fee_gate: %s gross @TP1 €%.2f < required €%.2f "
                "(fees €%.2f + min_net €%.2f); shares=%d, TP1=%.2f, entry=%.2f",
                _t, _gross_profit_eur, _required, _fees_roundtrip,
                config.MIN_NET_PROFIT_EUR, _shares, _tp1, _entry,
            )
            log_gate(_t, "fee_gate", True,
                     f"gross @TP1 €{_gross_profit_eur:.2f} < €{_required:.2f}",
                     {"gross_profit_eur": round(_gross_profit_eur, 2),
                      "fees_roundtrip_eur": _fees_roundtrip,
                      "min_net_eur": config.MIN_NET_PROFIT_EUR,
                      "shares": _shares, "tp1": _tp1, "entry": _entry})
            entry_recommendation = None

    if entry_recommendation:
        log_gate(
            entry_recommendation.get("ticker", "?"), "all_passed", False, "entry approved",
            {"size_eur": entry_recommendation.get("size_eur"),
             "conviction": entry_recommendation.get("conviction"),
             "p_win": entry_recommendation.get("p_win")},
        )
        # Stamp regime + VIX so downstream alpha-attribution + regime-conditional hit-rate
        # has the entry context, even if regime shifts mid-trade.
        _vix_at_entry = (market_ctx.get("^VIX") or {}).get("price")
        rec = {
            **entry_recommendation,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status": "pending",
            "regime_at_entry": regime,
            "vix_at_entry": _vix_at_entry if isinstance(_vix_at_entry, (int, float)) else None,
        }
        _ticker = rec.get("ticker", "?")
        _entry = rec.get("entry_price", 0)
        _sl = rec.get("stop_loss", 0)
        _tp = rec.get("take_profit", [])
        _size = rec.get("size_eur", 0)
        _conv = rec.get("conviction", 0)
        _hmin = rec.get("hold_days_min", "?")
        _hmax = rec.get("hold_days_max", "?")
        _thesis = rec.get("thesis", "")
        _trail = rec.get("trailing_stop_pct")
        _tp_str = " / ".join(f"€{t:.2f}" for t in (_tp if isinstance(_tp, list) else [_tp]))
        _trail_line = f"\nTrailing: {_trail}%" if _trail else ""
        # Shares preview + capital share — user needs to see size, not just euros
        _capital = float(_pf_snapshot.get("total_capital_eur", config.BUDGET_EUR) or config.BUDGET_EUR)
        _cash = float(_pf_snapshot.get("cash_eur", 0) or 0)
        _shares_raw = _size / _entry if _entry > 0 else 0.0
        # Whole shares when ≥1 (TR round-down to int); Bruchstücke only unter 1 Stk.
        if _shares_raw >= 1:
            _shares_prev = float(int(_shares_raw))
            _shares_str = f"{int(_shares_prev)} Stk"
        else:
            _shares_prev = round(_shares_raw, 2)
            _shares_str = f"{_shares_prev:.2f} Stk (Bruchstück)"
        _actual_size = round(_shares_prev * _entry, 2)
        _pct_cap = (_actual_size / _capital * 100) if _capital > 0 else 0.0
        _risk_eur = (_entry - _sl) * _shares_prev if _entry > _sl > 0 else 0.0
        _risk_pct = (_risk_eur / _capital * 100) if _capital > 0 else 0.0
        # Red-Team Review Block (wenn vorhanden): zeigt Bear-Sicht direkt am Alert,
        # damit User Conviction des Bots vs Bear-Critic vergleichen kann.
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

        # Anchor message for reply-based /confirm. User replies `/confirm 3` on this post.
        # Schema header (🎯 ACTION | TICKER | SIZE) matches send_actionable() format so
        # all actionable telegrams visually rhyme. Rich body needed for /confirm flow.
        message_id = _notify(
            f"🎯 *ENTRY* | `{_ticker}` | {_shares_str} à €{_entry:.2f} = €{_actual_size:.2f}\n"
            f"Grund: {_thesis}\n"
            f"Conv {_conv}/5\n"
            f"SL €{_sl:.2f} | TP {_tp_str} | Risk €{_risk_eur:.2f} ({_risk_pct:.2f}% Kap.) | "
            f"Hold {_hmin}-{_hmax}d | Cash €{_cash:.0f}{_trail_line}"
            f"{_rt_block}\n"
            f"_Reply `/confirm` (auto={_shares_str}) oder `/confirm <stück> @<preis>` für override._"
        )
        if message_id:
            rec["message_id"] = message_id
        if MEMPALACE_AVAILABLE:
            log_trade(rec, "RECOMMENDED", rec.get("thesis", ""))
        logger.info("Entry recommendation: %s @ €%.2f (msg_id=%s)", _ticker, _entry, message_id)

    # --- ADD recommendation handling (pyramiding into existing position) ---
    add_rec_persisted = None
    if add_recommendation:
        _at = (add_recommendation.get("ticker") or "").upper()
        _add_size = float(add_recommendation.get("additional_size_eur") or 0)
        _add_pf = load_portfolio()
        _open_pos = next(
            (tr for tr in _add_pf.get("open_trades", [])
             if (tr.get("ticker") or "").upper() == _at),
            None,
        )
        if not _open_pos:
            log_gate(_at, "add_no_position", True,
                     "ADD on ticker without open position", {})
            logger.info("ADD suppressed: %s has no open position", _at)
        elif _add_size <= 0:
            log_gate(_at, "add_invalid_size", True,
                     f"additional_size_eur={_add_size}", {})
            logger.info("ADD suppressed: %s invalid size %s", _at, _add_size)
        else:
            _orig_entry = float(_open_pos.get("entry_price") or 0)
            _orig_size = float(_open_pos.get("size_eur") or 0)
            _orig_sl = float(_open_pos.get("stop_loss") or 0)
            _md = market_data.get(_at) or {}
            _curr = _md.get("price")
            _atr = _md.get("atr14")

            _drift_ok = True
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
                    _drift_ok = False

            _sl_ok = True
            if _drift_ok and isinstance(_curr, (int, float)) and _orig_sl > 0:
                if float(_curr) <= _orig_sl:
                    log_gate(_at, "add_sl_breached", True,
                             f"price {_curr} ≤ original SL {_orig_sl}",
                             {"curr": _curr, "orig_sl": _orig_sl})
                    logger.info("ADD suppressed: %s current %.2f ≤ original SL %.2f",
                                _at, _curr, _orig_sl)
                    _sl_ok = False

            # Oversize-cap was broken when existing position > MAX_POSITION_SIZE_PERCENT
            # (legacy entries via /confirm bypass that cap). Full-trust user + drift/sl
            # gates + Claude conviction sufficient. User can /add manually with smaller
            # size if Claude over-bets (Audit 2026-04-28 RWE.DE €242 / 24% block).
            if _drift_ok and _sl_ok:
                _trigger = (add_recommendation.get("trigger") or "")[:120]
                _reinforce = (add_recommendation.get("thesis_reinforcement") or "")[:120]
                _add_conv = add_recommendation.get("conviction")
                add_msg_id = send_actionable(
                    "ADD",
                    _at,
                    f"+€{_add_size:.0f}",
                    reason=_trigger or _reinforce or "Setup-Verstärkung",
                    conviction=_add_conv if isinstance(_add_conv, int) else None,
                    extras={
                        "Verstärkung": _reinforce,
                        "Bestand": f"€{_orig_size:.0f} @ €{_orig_entry:.2f}",
                        "SL": f"€{_orig_sl:.2f}",
                    },
                )
                add_rec_persisted = {
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
                log_gate(_at, "add_all_passed", False, "ADD approved",
                         {"add_size": _add_size, "orig_size": _orig_size})
                logger.info("ADD recommendation: %s +€%.2f (msg_id=%s)",
                            _at, _add_size, add_msg_id)

    # --- UPDATE_POSITION_TARGETS handling ---
    update_persisted = None
    if update_targets:
        _ut = (update_targets.get("ticker") or "").upper()
        _new_sl = update_targets.get("new_stop_loss")
        _new_tp = update_targets.get("new_take_profit")
        _ureason = (update_targets.get("reason") or "").strip()[:120]
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
        elif _new_sl is None and _new_tp is None:
            log_gate(_ut, "update_empty", True, "neither SL nor TP set", {})
            logger.info("Update suppressed: %s neither SL nor TP set", _ut)
        elif not _ureason:
            log_gate(_ut, "update_no_reason", True, "reason empty", {})
            logger.info("Update suppressed: %s no reason", _ut)
        else:
            _orig_entry = float(_upos.get("entry_price") or 0)
            _orig_sl = float(_upos.get("stop_loss") or 0)
            _sl_ok = True
            if _new_sl is not None:
                _new_sl = float(_new_sl)
                # SL must stay below entry (LONG-only) and only move UP (lock-in profit
                # OR breakeven). Moving SL down = loosening protection, never allowed
                # via Claude recommendation — would violate full-trust safety.
                if _new_sl >= _orig_entry:
                    log_gate(_ut, "update_sl_above_entry", True,
                             f"new_sl {_new_sl} >= entry {_orig_entry}",
                             {"new_sl": _new_sl, "entry": _orig_entry})
                    logger.info("Update suppressed: %s SL above entry", _ut)
                    _sl_ok = False
                elif _orig_sl > 0 and _new_sl < _orig_sl:
                    log_gate(_ut, "update_sl_lowered", True,
                             f"new_sl {_new_sl} < orig_sl {_orig_sl} — loosening forbidden",
                             {"new_sl": _new_sl, "orig_sl": _orig_sl})
                    logger.info("Update suppressed: %s SL lowered (loosening forbidden)", _ut)
                    _sl_ok = False
            _tp_ok = True
            _tp_norm = None
            if _new_tp is not None:
                _tp_norm = _new_tp if isinstance(_new_tp, list) else [_new_tp]
                _tp_norm = [float(x) for x in _tp_norm]
                if any(x <= _orig_entry for x in _tp_norm):
                    log_gate(_ut, "update_tp_below_entry", True,
                             f"tp {_tp_norm} has value ≤ entry {_orig_entry}",
                             {"new_tp": _tp_norm, "entry": _orig_entry})
                    logger.info("Update suppressed: %s TP ≤ entry", _ut)
                    _tp_ok = False

            if _sl_ok and _tp_ok:
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
                update_persisted = {
                    "kind": "update",
                    "ticker": _ut,
                    "new_stop_loss": _new_sl,
                    "new_take_profit": _tp_norm,
                    "reason": _ureason,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "status": "pending",
                    "message_id": upd_msg_id,
                }
                log_gate(_ut, "update_all_passed", False, "SL/TP update approved", {
                    "new_sl": _new_sl, "new_tp": _tp_norm,
                })
                logger.info("Update recommendation: %s SL=%s TP=%s (msg_id=%s)",
                            _ut, _new_sl, _tp_norm, upd_msg_id)

    # --- RECOMMEND_EXIT handling ---
    exit_persisted = None
    if exit_recommendation:
        _et = (exit_recommendation.get("ticker") or "").upper()
        _ereason = (exit_recommendation.get("reason") or "").strip()[:120]
        _eurg = (exit_recommendation.get("urgency") or "today").lower()
        if _eurg not in {"now", "today", "eod"}:
            _eurg = "today"
        _epf = load_portfolio()
        _epos = next(
            (tr for tr in _epf.get("open_trades", [])
             if (tr.get("ticker") or "").upper() == _et),
            None,
        )
        if not _epos:
            log_gate(_et, "exit_no_position", True,
                     "recommend_exit without open position", {})
            logger.info("Exit suppressed: %s no open position", _et)
        elif not _ereason:
            log_gate(_et, "exit_no_reason", True, "reason empty", {})
            logger.info("Exit suppressed: %s no reason", _et)
        else:
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
            exit_msg_id = _notify(
                f"🎯 *EXIT* | `{_et}` | {_urgency_emoji} {_eurg}\n"
                f"Grund: {_ereason}\n"
                f"Bestand: €{_orig_size:.0f} @ €{_orig_entry:.2f} | Jetzt: {_curr_str} ({_pnl_str})\n"
                f"_Reply `/confirm` um auf TR zu schließen, dann `/close {_et} @PREIS [#tag]`._"
            )
            exit_persisted = {
                "kind": "exit",
                "ticker": _et,
                "reason": _ereason,
                "urgency": _eurg,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "status": "pending",
                "message_id": exit_msg_id,
            }
            log_gate(_et, "exit_all_passed", False, "exit recommended", {"urgency": _eurg})
            logger.info("Exit recommendation: %s urgency=%s (msg_id=%s)",
                        _et, _eurg, exit_msg_id)

    if MEMPALACE_AVAILABLE:
        log_analysis(analysis_text, mode, event_context)

    # Correlation snapshot for the dashboard: full pairwise matrix over open
    # positions, refreshed on morning analysis. Cheap (already pulling returns
    # for the corr-gate) and avoids re-computing in JS.
    corr_matrix = None
    if mode == "morning":
        corr_matrix = compute_correlation_snapshot(portfolio)

    # Per-mode trace — every stage logged + persisted for /brain + dashboard.
    # User-requested 2026-04-29: "Dann sehen wir wurde nicht gesetzt weil gab keine
    # guten" — trace makes pipeline visible end-to-end. Originally morning-only;
    # generalized to opening/event after the same truncation failure-mode could
    # silently mask their tool-calls too (max_tokens=200/300 even tighter).
    trace: dict | None = None
    trace_key = _trace_key(mode, event_context)
    if trace_key is not None:
        trace = _build_trace(
            mode, response,
            watch_tool_called=watch_tool_called,
            watch_tool_raw_input=watch_tool_raw_input,
            new_levels=new_levels,
            text_parts=text_parts,
            max_tokens=max_tokens,
        )
        _log_trace_warnings(trace)

    # Reload-merge save: protects concurrent writes from the Telegram listener
    # (e.g. /confirm that moves a pending_rec into open_trades).
    with portfolio_lock:
        fresh = load_portfolio()
        if corr_matrix is not None:
            fresh["correlation_matrix"] = corr_matrix
        if trace is not None and trace_key is not None:
            fresh[trace_key] = trace
        if new_levels is not None:
            filtered = [lvl for lvl in new_levels if lvl.get("ticker") not in excluded]
            dropped = len(new_levels) - len(filtered)
            if dropped:
                logger.warning("Dropped %d watch level(s) on excluded tickers", dropped)
                if trace is not None:
                    trace["dropped_excluded"] = dropped

            # Self-sabotage filter: drop resistance_reject whose trigger sits between
            # an open trade's entry and TP1. Such a level fires on the natural tag-and-
            # continue of a breakout, killing the trade before it reaches its own
            # target. (Bug 2026-04-27: RWE breakout @60.6 with TP1 62.4 + watch_reject
            # @60.7 → 15-min snapshot saw tag-and-dip → bot recommended EXIT while
            # breakout was actually working.)
            _open_by_ticker = {t["ticker"]: t for t in fresh.get("open_trades", [])}
            _conflict_filtered = []
            _dropped_self_sabotage = 0
            for lvl in filtered:
                if lvl.get("type") != "resistance_reject":
                    _conflict_filtered.append(lvl)
                    continue
                _ot = _open_by_ticker.get(lvl.get("ticker"))
                if not _ot:
                    _conflict_filtered.append(lvl)
                    continue
                _entry = float(_ot.get("entry_price") or 0)
                _tp = _ot.get("take_profit")
                _tp1 = float(_tp[0]) if isinstance(_tp, list) and _tp else (
                    float(_tp) if isinstance(_tp, (int, float)) else 0
                )
                _trig = float(lvl.get("trigger_price") or 0)
                if _entry > 0 and _tp1 > _entry and _entry < _trig <= _tp1:
                    logger.warning(
                        "Dropped self-sabotage watch_resistance_reject %s @%.2f "
                        "(sits between entry %.2f and TP1 %.2f of open breakout)",
                        lvl.get("ticker"), _trig, _entry, _tp1,
                    )
                    _dropped_self_sabotage += 1
                    continue
                _conflict_filtered.append(lvl)
            filtered = _conflict_filtered
            if trace is not None and _dropped_self_sabotage:
                trace["dropped_self_sabotage"] = _dropped_self_sabotage

            # Whole-share filter on incoming levels: TR-SL braucht ganze Stücke,
            # kein Sinn Watch-Level für Tickers zu setzen, die wir nicht handeln können.
            # Preis-Quelle: market_data (frisch aus diesem Run); fallback auf level.current_price.
            _max_share_price_lvl = max_affordable_share_price_eur(fresh)
            _kept_lvls = []
            _dropped_unaffordable_lvls = []
            for lvl in filtered:
                _lt = (lvl.get("ticker") or "").upper()
                _md = market_data.get(_lt) or {}
                _lp = _md.get("price")
                if not isinstance(_lp, (int, float)):
                    _lp = lvl.get("current_price")
                if isinstance(_lp, (int, float)) and _lp > _max_share_price_lvl:
                    _dropped_unaffordable_lvls.append(f"{_lt}(€{_lp:.2f})")
                    continue
                _kept_lvls.append(lvl)
            if _dropped_unaffordable_lvls:
                logger.info("Watch-levels dropped (price>€%.2f): %s",
                            _max_share_price_lvl, ", ".join(_dropped_unaffordable_lvls))
                if trace is not None:
                    trace["dropped_unaffordable"] = len(_dropped_unaffordable_lvls)
            filtered = _kept_lvls

            # Merge-by-ticker instead of full replace: Sonnet returning [] used to
            # WIPE all morning levels (Bug 2026-04-28: 08:00 set_watch_levels([]) →
            # 0 watchlevels for the day → no event-mode entries possible).
            # Now: empty list = "no new setups today" — existing valid levels stay.
            # Non-empty list = replace entries WHERE ticker matches; other tickers stay.
            existing = fresh.get("watch_levels", [])
            if not filtered:
                # Empty new set → keep existing (let events.py decay handle expiries).
                # Morning mode: Sonnet MUSS ≥3 Levels liefern (prompt-mandate).
                # 0 Levels = Sonnet versagte oder Tool-Call wurde truncated.
                if mode == "morning":
                    logger.error(
                        "MORNING WATCHLEVEL FAIL: Sonnet returned 0 levels (existing=%d). "
                        "Prompt requires ≥3. Check max_tokens / regime-defensiveness.",
                        len(existing),
                    )
                else:
                    logger.info(
                        "Watch levels: Sonnet returned 0 new levels — keeping %d existing",
                        len(existing),
                    )
                if trace is not None:
                    trace["final_count"] = len(existing)
                    trace["kept_existing"] = len(existing)
                    trace["new_set"] = 0
                    trace["final_tickers"] = [
                        (lvl.get("ticker") or "?") for lvl in existing
                    ]
            else:
                new_tickers = {(lvl.get("ticker") or "").upper() for lvl in filtered}
                kept = [
                    lvl for lvl in existing
                    if (lvl.get("ticker") or "").upper() not in new_tickers
                ]
                merged = kept + filtered
                fresh["watch_levels"] = merged
                logger.info(
                    "Watch levels merged: %d kept (other tickers) + %d new = %d total",
                    len(kept), len(filtered), len(merged),
                )
                if trace is not None:
                    trace["final_count"] = len(merged)
                    trace["kept_existing"] = len(kept)
                    trace["new_set"] = len(filtered)
                    trace["final_tickers"] = [
                        (lvl.get("ticker") or "?") for lvl in merged
                    ]
        if rec is not None:
            fresh.setdefault("pending_recommendations", []).append(rec)
        if add_rec_persisted is not None:
            fresh.setdefault("pending_recommendations", []).append(add_rec_persisted)
        if update_persisted is not None:
            fresh.setdefault("pending_recommendations", []).append(update_persisted)
        if exit_persisted is not None:
            # Two-Stage Suppress (User-Feedback 2026-05-04 zu RWE.DE Spam):
            # (a) Cooldown: wenn dieser Trade kürzlich einen auto-dropped Exit-Rec
            #     hatte (≤EXIT_REC_COOLDOWN_MIN_AFTER_DROP), suppress neue Exit-Recs
            #     komplett. Verhindert Spiral nach User-Ignore + Auto-Drop.
            # (b) Keep-Existing: wenn schon ein pending Exit für Ticker liegt, NICHT
            #     ersetzen — sonst resettet Timer + User bekommt erneut 1. Reminder.
            #     User hat ersten Alert schon gesehen; wenn sie ignorieren, geht's
            #     durch den Reminder→2nd-Reminder→Drop-Flow.
            _et = (exit_persisted.get("ticker") or "").upper()
            _open_pos = next(
                (tr for tr in fresh.get("open_trades", []) or []
                 if (tr.get("ticker") or "").upper() == _et),
                None,
            )
            _in_cooldown = False
            if _open_pos and _open_pos.get("exit_dropped_at"):
                try:
                    _drop_dt = datetime.strptime(
                        _open_pos["exit_dropped_at"], "%Y-%m-%d %H:%M",
                    )
                    _age_min = (datetime.now() - _drop_dt).total_seconds() / 60.0
                    if _age_min < config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP:
                        _in_cooldown = True
                        log_gate(_et, "exit_cooldown", True,
                                 f"in cooldown {_age_min:.0f}min < "
                                 f"{config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP}min",
                                 {"age_min": round(_age_min, 1),
                                  "cooldown_min": config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP})
                        logger.info(
                            "Exit-rec for %s suppressed (cooldown %.0fmin < %dmin)",
                            _et, _age_min, config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP,
                        )
                except ValueError:
                    pass

            if not _in_cooldown:
                _existing = fresh.get("pending_recommendations", []) or []
                _has_pending = any(
                    r.get("kind") == "exit"
                    and (r.get("ticker") or "").upper() == _et
                    for r in _existing
                )
                if _has_pending:
                    log_gate(_et, "exit_dedupe", True,
                             "existing pending exit-rec, keep old (no timer reset)", {})
                    logger.info(
                        "Exit-rec for %s suppressed (existing pending kept)", _et,
                    )
                else:
                    fresh.setdefault("pending_recommendations", []).append(exit_persisted)
        fresh["last_analysis"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_portfolio(fresh)

    # Paper-Portfolio Auto-Open (außerhalb des real-portfolio_lock — eigener paper_lock).
    # Lernt parallel ohne User-Action: jeder rec, der alle Gates passt, wird auch im
    # Trainings-Portfolio geöffnet. Fail-soft: niemals echten Flow blockieren.
    if rec is not None:
        try:
            _auto_paper_open(rec)
        except Exception:
            logger.exception("paper-portfolio auto-open failed")

    return analysis_text
