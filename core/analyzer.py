"""Claude-powered portfolio analysis orchestrator.

Composes market data + portfolio state + macro + prompts into a single Claude
call, parses tool_use blocks, dispatches each rec-type to the matching handler
in core.tool_handlers, and persists results under the portfolio lock.

Engine-gate enforcement lives in core.tool_handlers.
Trace + metrics live in core.trace / core.metrics.
"""

import re
import logging
from datetime import datetime

from anthropic import Anthropic
from dotenv import load_dotenv

import config
from memory import log_analysis, build_history_context, MEMPALACE_AVAILABLE
from macro import today_events as _today_macro_events, format_events as _format_macro_events

from core.api_usage import can_make_api_call, increment_usage
from core.call_log import log_claude_call
from core.metrics import compute_correlation_snapshot
from core.prompts import (
    STRATEGY_SYSTEM, MORNING_PREP_PROMPT, OPENING_CHECK_PROMPT, EVENT_TRIGGER_PROMPT,
    WATCH_LEVELS_TOOL, RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
    UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL, SUBMIT_PASS_TOOL,
)
from core.portfolio import (
    portfolio_lock, load_portfolio, save_portfolio, suggest_position_size,
    compute_hit_stats, format_hit_stats,
    compute_portfolio_heat, format_portfolio_heat,
    compute_sector_exposure, format_sector_exposure,
    compute_equity_stats, format_equity_stats,
    maintain_drawdown_state,
    compute_confluence,
    max_affordable_share_price_eur,
    active_entry_gate_cooldowns,
)
from core.market_data import (
    get_market_data, get_earnings_warnings, fetch_news, market_regime,
)
from core.state_classifier import classify_state
from core.tool_handlers import (
    compact, dump, build_thesis_degradation_lines,
    handle_entry_recommendation, handle_add_recommendation,
    handle_update_targets, handle_exit_recommendation,
    auto_paper_open,
)
from core.trace import build_trace, log_trace_warnings, trace_key


load_dotenv()
logger = logging.getLogger(__name__)

client = Anthropic()


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

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

    # DD-state hysteresis maintenance.
    with portfolio_lock:
        _fresh = load_portfolio()
        if maintain_drawdown_state(_fresh):
            save_portfolio(_fresh)

    portfolio = load_portfolio()

    open_trade_tickers = [t["ticker"] for t in portfolio.get("open_trades", [])]
    watch_level_tickers = [w["ticker"] for w in portfolio.get("watch_levels", [])]
    excluded = set(config.EXCLUDED_TICKERS)
    tradeable = [
        t for t in set(open_trade_tickers + watch_level_tickers
                       + config.WATCHLIST + config.COMMODITIES)
        if t not in excluded
    ]
    market_tickers = list(config.MARKET_INDICATORS)

    market_data = get_market_data(tradeable)
    market_ctx = get_market_data(market_tickers)

    # Liquidity + whole-share pre-filter. Protected set = open + watch tickers
    # (always visible for exit/breakout decisions). Opening mode loosens the
    # volume gate since 5min-vol vs daily-avg is inherently tiny at open.
    _max_share_price = max_affordable_share_price_eur(portfolio)
    _kept = {}
    _dropped_illiquid: list[str] = []
    _dropped_unaffordable: list[str] = []
    _protected = set(open_trade_tickers) | set(watch_level_tickers)
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
        # vol_ratio==0 = no aggregated daily volume yet (pre-open, post-weekend).
        # Treat as no-data; only gate positive-but-thin values.
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

    # Entry-gate cooldown annotation: tickers that recently failed RS/edge/red-team
    # get an `entry_cooldown` marker. Claude sees it and skips re-recommending.
    # The hard gate still re-checks live data downstream, so a real improvement
    # is never missed.
    _entry_cooldowns = active_entry_gate_cooldowns(portfolio)
    for _ct, _cd in _entry_cooldowns.items():
        _cdata = market_data.get(_ct)
        if isinstance(_cdata, dict) and not _cdata.get("error"):
            _cdata["entry_cooldown"] = (
                f"{_cd.get('gate')}: {_cd.get('reason')} — KEIN recommend_entry, "
                f"Gate würde ohnehin blocken (Cooldown bis "
                f"{config.ENTRY_GATE_COOLDOWN_MIN}min nach Fail)"
            )

    regime = market_regime(market_ctx)
    cash = portfolio.get("cash_eur", config.BUDGET_EUR)
    atr_sizes = {
        t: suggest_position_size(market_data[t].get("atr14_pct"), cash)
        for t in tradeable if t in market_data and not market_data[t].get("error")
    }

    # Precompute categorical state per ticker. Python classifies, LLM references
    # state.entry_state etc. instead of re-deriving from raw indicators.
    for _t, _d in market_data.items():
        if isinstance(_d, dict) and not _d.get("error"):
            _d["state"] = classify_state(_d, regime)

    # ---- Pick prompt + tools by mode ----
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
        # Event-mode darf set_watch_levels nicht überschreiben (Morning-Domain).
        tools = [
            RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
            UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL, SUBMIT_PASS_TOOL,
        ]
    else:
        system_prompt = STRATEGY_SYSTEM
        context_intro = "Standard-Analyse"

    # ---- MemPalace history context ----
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

    # ---- Mistake summary (class distribution over last 20 losses) ----
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

    # ---- Trim market_ctx to essentials (saves ~80% of those tokens) ----
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

    # Format-Header obsolete (2026-05-22): Architektur jetzt Tool-Calls-only,
    # Text-Output Backend-seitig verworfen in run_morning_prep.
    format_header = ""

    analysis_request = f"""{format_header}{context_intro}

Datum/Zeit: {datetime.now().strftime("%Y-%m-%d %H:%M")}

## Portfolio
Kapital: €{portfolio.get('total_capital_eur', config.BUDGET_EUR):.2f}
Cash: €{portfolio.get('cash_eur', config.BUDGET_EUR):.2f}

## Offene Positionen
{dump(portfolio.get('open_trades', [])) if portfolio.get('open_trades') else "Keine"}

## Aktive Watch Levels
{dump(portfolio.get('watch_levels', [])) if portfolio.get('watch_levels') else "Keine definiert"}

## Markt-Kontext (Indizes + VIX)
{dump(_market_ctx_slim)}

## Live Kurse (mit RSI, MACD, MA20/50, BB, VWAP, Intraday-OHLC, Analyst-Konsens)
{dump(market_data)}

## Markt-Regime
{regime}

## ATR-basierte Positionsgrößen (€{cash:.0f} Cash, {config.MAX_RISK_PER_TRADE_PERCENT}% Risiko/Trade)
{dump(atr_sizes)}
"""

    # ---- Optional context blocks ----
    _degr_lines = build_thesis_degradation_lines(
        portfolio.get("open_trades", []), market_data,
    )
    if _degr_lines:
        analysis_request += "\n\n## THESIS-STATUS (Snapshot vs. Jetzt)\n" + "\n".join(_degr_lines) + "\n"

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

    if mode in ("morning", "opening"):
        heat = compute_portfolio_heat(portfolio)
        analysis_request += f"\n\n## PORTFOLIO HEAT\n{format_portfolio_heat(heat)}\n"

    if mode in ("morning", "opening"):
        sectors = compute_sector_exposure(portfolio)
        if sectors:
            analysis_request += f"\n\n## SECTOR EXPOSURE\n{format_sector_exposure(sectors)}\n"

    if mode in ("morning", "opening"):
        macro = _today_macro_events()
        if macro:
            analysis_request += f"\n\n## ⚠️ HEUTE: HIGH-IMPACT EVENTS\n{_format_macro_events(macro)}\n"

    if mode == "morning":
        eq = compute_equity_stats(
            portfolio.get("closed_trades", []),
            config.BUDGET_EUR,
            portfolio.get("cash_movements", []),
        )
        if eq:
            analysis_request += f"\n\n## EQUITY CURVE\n{format_equity_stats(eq)}\n"

    if mode == "morning":
        stats = compute_hit_stats(portfolio.get("closed_trades", []),
                                  portfolio.get("cash_movements", []))
        if stats:
            if stats.get("class_suggestion"):
                logger.warning("Self-calibration: %s", stats["class_suggestion"])
            analysis_request += f"\n\n## HIT-RATE (eigene History)\n{format_hit_stats(stats)}\n"
    elif mode in ("event", "opening"):
        # Surface haircut for event/opening too so Haiku doesn't blindly mirror
        # historical bias (Bug 2026-05-07: under-confident haircut=-0.38 silently
        # killed CON.DE +9% entry).
        stats = compute_hit_stats(portfolio.get("closed_trades", []),
                                  portfolio.get("cash_movements", []))
        cal = (stats or {}).get("calibration") or {}
        if cal.get("haircut"):
            hc = cal["haircut"]
            direction = (
                f"Bot war historisch ZU PESSIMISTISCH (haircut={hc:+.2f}, edge gate "
                f"addiert {abs(min(0.20, abs(hc))):+.2f} auto auf dein p_win) — sei AGGRESSIVER"
                if hc < 0 else
                f"Bot war historisch ZU OPTIMISTISCH (haircut={hc:+.2f}, edge gate "
                f"zieht {min(0.20, abs(hc)):.2f} auto von deinem p_win ab) — sei STRENGER"
            )
            analysis_request += f"\n\n## BRIER-CAL\n{direction}\n"

    if mode in ("morning", "opening", "event"):
        conf_lines = []
        for _t, _d in market_data.items():
            if not isinstance(_d, dict) or _d.get("error"):
                continue
            if _t in config.MARKET_INDICATORS or _t in config.COMMODITIES:
                continue
            _c = compute_confluence(_d, regime)
            if _c["score"] >= 4:  # only non-trivial scores
                hits = [k for k, v in _c["items"].items() if v]
                conf_lines.append(f"  {_t}: {_c['score']}/10 — {', '.join(hits)}")
        if conf_lines:
            analysis_request += (
                f"\n\n## CONFLUENCE-SCORES\n" + "\n".join(conf_lines) + "\n"
            )

    if mode == "morning":
        earnings_tickers = (
            [t["ticker"] for t in portfolio.get("open_trades", [])]
            + config.WATCHLIST + config.COMMODITIES
        )
        ew = get_earnings_warnings(list(set(earnings_tickers)), days_ahead=3)
        if ew:
            ew_lines = "\n".join(
                f"  ⚠️ {w['ticker']}: Earnings in {w['days_until']} Tag(en) ({w['earnings_date']})"
                for w in ew
            )
            analysis_request += f"\n\n## Earnings Kalender (nächste 3 Tage)\n{ew_lines}\n"

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

    # ---- Output budgets per mode ----
    # Morning needs room for set_watch_levels(3-7 levels) serialized in tool_input.
    # 2026-04-29: Sonnet 400 tokens → set_watch_levels({}) empty input → 0 levels.
    # 2026-05-07: opening hit 500/500 twice, both dropped recommend_entry.
    # 2026-05-20: morning hit 1200 — manifest-2 rewrite + zone_low/high schema.
    # 2026-05-22: bumped morning 2500→3500 for tool_choice=any + v7-required-fields.
    if mode == "morning":
        max_tokens = 3500
    elif mode == "opening":
        max_tokens = 900
    elif mode == "event":
        max_tokens = 900
    else:
        max_tokens = 400

    # Sonnet only for morning. Opening/event use Haiku.
    model = config.CLAUDE_MODEL_MORNING if mode == "morning" else config.CLAUDE_MODEL_EVENT

    create_kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        "messages": [{"role": "user", "content": analysis_request}],
    }
    if tools:
        create_kwargs["tools"] = tools

    # tool_choice="any" forces model to use one of the provided tools.
    # Free-text alongside is allowed but discarded backend-side.
    if tools and mode in ("morning", "event", "opening"):
        create_kwargs["tool_choice"] = {"type": "any"}

    response = client.messages.create(**create_kwargs)
    increment_usage(forced=is_high_priority)

    usage = getattr(response, "usage", None)
    if usage is not None:
        logger.info(
            "Claude call: mode=%s turn=1 in=%s out=%s cache_read=%s cache_write=%s",
            mode,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
            getattr(usage, "cache_read_input_tokens", None),
            getattr(usage, "cache_creation_input_tokens", None),
        )

    # ---- Extract text + tool_use blocks ----
    text_parts: list[str] = []
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
        usage=usage,
        extra={"event_context": event_context} if event_context else None,
    )

    # Dedupe consecutive identical lines (Sonnet sometimes emits same status
    # line in multiple text-blocks when tool_use is sandwiched between).
    _raw_text = "\n".join(t for t in text_parts if t).strip()
    _dedup: list[str] = []
    for _ln in _raw_text.split("\n"):
        if _dedup and _ln.strip() == _dedup[-1].strip():
            continue
        _dedup.append(_ln)
    if not _dedup and pass_reason:
        analysis_text = f"PASS: {pass_reason}"
    else:
        analysis_text = "\n".join(_dedup) or "(keine Text-Analyse)"

    # ---- Dispatch each tool-call to its handler ----
    rec: dict | None = None
    if entry_recommendation:
        rec = handle_entry_recommendation(
            entry_recommendation,
            mode=mode,
            market_data=market_data,
            market_ctx=market_ctx,
            regime=regime,
            cash=cash,
            model=model,
        )

    add_rec_persisted = handle_add_recommendation(
        add_recommendation, market_data,
    ) if add_recommendation else None

    update_persisted = handle_update_targets(update_targets) if update_targets else None

    exit_persisted = handle_exit_recommendation(
        exit_recommendation, market_data,
    ) if exit_recommendation else None

    if MEMPALACE_AVAILABLE:
        log_analysis(analysis_text, mode, event_context)

    corr_matrix = compute_correlation_snapshot(portfolio) if mode == "morning" else None

    # Per-mode trace for /brain dashboard + Telegram visibility.
    trace: dict | None = None
    _tkey = trace_key(mode, event_context)
    if _tkey is not None:
        trace = build_trace(
            mode, response,
            watch_tool_called=watch_tool_called,
            watch_tool_raw_input=watch_tool_raw_input,
            new_levels=new_levels,
            text_parts=text_parts,
            max_tokens=max_tokens,
        )
        log_trace_warnings(trace)

    # ---- Persist phase (reload-merge for concurrent /confirm safety) ----
    with portfolio_lock:
        fresh = load_portfolio()
        if corr_matrix is not None:
            fresh["correlation_matrix"] = corr_matrix
        if trace is not None and _tkey is not None:
            fresh[_tkey] = trace

        if new_levels is not None:
            _persist_watch_levels(
                fresh, new_levels, excluded, market_data, mode, trace,
            )

        if rec is not None:
            fresh.setdefault("pending_recommendations", []).append(rec)
        if add_rec_persisted is not None:
            fresh.setdefault("pending_recommendations", []).append(add_rec_persisted)
        if update_persisted is not None:
            fresh.setdefault("pending_recommendations", []).append(update_persisted)
        if exit_persisted is not None:
            _persist_exit_with_cooldown_dedupe(fresh, exit_persisted)

        fresh["last_analysis"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_portfolio(fresh)

    # Paper-portfolio mirror runs outside the real-portfolio lock (own paper_lock).
    if rec is not None:
        try:
            auto_paper_open(rec)
        except Exception:
            logger.exception("paper-portfolio auto-open failed")

    return analysis_text


# ---------------------------------------------------------------------------
# Persist helpers (inline because they need analyze_portfolio's `fresh`)
# ---------------------------------------------------------------------------

def _persist_watch_levels(
    fresh: dict,
    new_levels: list,
    excluded: set,
    market_data: dict,
    mode: str,
    trace: dict | None,
) -> None:
    """Merge new watch-levels into portfolio. Filters: excluded tickers,
    self-sabotage resistance_reject (between entry and TP1), unaffordable
    (price > max-share-price). Empty new set keeps existing (no wipe)."""
    filtered = [lvl for lvl in new_levels if lvl.get("ticker") not in excluded]
    dropped = len(new_levels) - len(filtered)
    if dropped:
        logger.warning("Dropped %d watch level(s) on excluded tickers", dropped)
        if trace is not None:
            trace["dropped_excluded"] = dropped

    # Self-sabotage filter: drop resistance_reject between own entry and TP1.
    # (Bug 2026-04-27: RWE breakout @60.6 with TP1 62.4 + watch_reject @60.7 →
    # tag-and-dip read as exit while breakout was working.)
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

    # Whole-share filter on incoming levels.
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

    # Merge-by-ticker (empty new = keep existing). Sonnet returning []
    # used to WIPE all morning levels — Bug 2026-04-28.
    existing = fresh.get("watch_levels", [])
    if not filtered:
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


def _persist_exit_with_cooldown_dedupe(fresh: dict, exit_persisted: dict) -> None:
    """Two-stage suppress for exit-recs:
    (a) Cooldown: skip if trade had recent auto-dropped exit-rec (≤cooldown_min).
    (b) Keep-existing: if pending exit already exists for ticker, don't replace
        (replacing resets reminder timer and re-pings user)."""
    _et = (exit_persisted.get("ticker") or "").upper()
    _open_pos = next(
        (tr for tr in fresh.get("open_trades", []) or []
         if (tr.get("ticker") or "").upper() == _et),
        None,
    )
    if _open_pos and _open_pos.get("exit_dropped_at"):
        try:
            _drop_dt = datetime.strptime(
                _open_pos["exit_dropped_at"], "%Y-%m-%d %H:%M",
            )
            _age_min = (datetime.now() - _drop_dt).total_seconds() / 60.0
            if _age_min < config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP:
                from core.gate_log import log_gate
                log_gate(_et, "exit_cooldown", True,
                         f"in cooldown {_age_min:.0f}min < "
                         f"{config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP}min",
                         {"age_min": round(_age_min, 1),
                          "cooldown_min": config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP})
                logger.info(
                    "Exit-rec for %s suppressed (cooldown %.0fmin < %dmin)",
                    _et, _age_min, config.EXIT_REC_COOLDOWN_MIN_AFTER_DROP,
                )
                return
        except ValueError:
            pass

    _existing = fresh.get("pending_recommendations", []) or []
    _has_pending = any(
        r.get("kind") == "exit"
        and (r.get("ticker") or "").upper() == _et
        for r in _existing
    )
    if _has_pending:
        from core.gate_log import log_gate
        log_gate(_et, "exit_dedupe", True,
                 "existing pending exit-rec, keep old (no timer reset)", {})
        logger.info("Exit-rec for %s suppressed (existing pending kept)", _et)
        return
    fresh.setdefault("pending_recommendations", []).append(exit_persisted)
