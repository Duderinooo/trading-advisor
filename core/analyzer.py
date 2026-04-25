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
from notifier import send_notification as _notify
from memory import (
    log_trade, log_analysis, build_history_context, MEMPALACE_AVAILABLE,
)
from macro import today_events as _today_macro_events, format_events as _format_macro_events

from core.api_usage import can_make_api_call, increment_usage
from core.prompts import (
    STRATEGY_SYSTEM, MORNING_PREP_PROMPT, OPENING_CHECK_PROMPT, EVENT_TRIGGER_PROMPT,
    WATCH_LEVELS_TOOL, RECOMMEND_ENTRY_TOOL,
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


# ---------- Main orchestrator ----------

def analyze_portfolio(mode: str = "standard", event_context: str = None, force: bool = False) -> str:
    """Run portfolio analysis with Claude.

    Modes:
    - "morning":  Full daily briefing + watch-level planning (Sonnet)
    - "opening":  Lightweight gap-check at market open (Haiku)
    - "event":    Quick analysis triggered by a watch-level hit / price-alert
    - "standard": Regular analysis

    force: Bypass regular cooldown for high-priority situations.
    """
    is_high_priority = mode in ("morning", "opening", "event") or force
    allowed, reason = can_make_api_call(force=is_high_priority)
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
    _kept = {}
    _dropped_illiquid = []
    _protected = set(open_trade_tickers) | set(watch_level_tickers)
    for _t, _d in market_data.items():
        if not isinstance(_d, dict) or _d.get("error"):
            _kept[_t] = _d
            continue
        if _t in _protected:
            _kept[_t] = _d
            continue
        vr = _d.get("volume_ratio")
        sp = _d.get("spread_pct")
        if vr is not None and vr < config.MIN_VOLUME_RATIO:
            _dropped_illiquid.append(f"{_t}(vol_ratio={vr})")
            continue
        if sp is not None and sp > config.MAX_SPREAD_PERCENT:
            _dropped_illiquid.append(f"{_t}(spread={sp}%)")
            continue
        _kept[_t] = _d
    if _dropped_illiquid:
        logger.info("Liquidity gate dropped: %s", ", ".join(_dropped_illiquid))
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
            "\n\nWICHTIG: Rufe IMMER `set_watch_levels` auf (auch mit leerer Liste). "
            "Bei A+-Setup mit Conviction ≥3/5: AUCH `recommend_entry` aufrufen."
        )
        context_intro = "MORNING"
        tools = [WATCH_LEVELS_TOOL, RECOMMEND_ENTRY_TOOL]
    elif mode == "opening":
        system_prompt = STRATEGY_SYSTEM + "\n\n" + OPENING_CHECK_PROMPT
        context_intro = f"OPEN-CHECK {event_context or ''}".strip()
        tools = [RECOMMEND_ENTRY_TOOL]
    elif mode == "event":
        system_prompt = STRATEGY_SYSTEM + "\n\n" + EVENT_TRIGGER_PROMPT
        system_prompt += "\n\nBei ENTRY-Empfehlung mit Conviction ≥3/5: `recommend_entry` aufrufen."
        context_intro = f"🚨 EVENT: {event_context}"
        tools = [WATCH_LEVELS_TOOL, RECOMMEND_ENTRY_TOOL]
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
            "🚨 OUTPUT-REGEL (ZWINGEND): Erste und einzige Zeile:\n"
            "  • `Alles stabil, keine Anpassungen.` (wenn nichts ändert sich)\n"
            "  • Oder GENAU eine Zeile pro actionable Item.\n"
            "KEIN Internal Analysis Block.\n\n"
        )
    elif mode == "event":
        format_header = (
            "🚨 OUTPUT-REGEL (ZWINGEND): Genau EINE Zeile, beginnend mit:\n"
            "  • `ENTRY: ...`  (bei A+ Setup mit Conv ≥3, zusätzlich recommend_entry Tool)\n"
            "  • `EXIT: ...`   (offene Position schließen)\n"
            "  • `PASS: TICKER | Grund`  (kein Edge — kurz warum)\n"
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
_(SPY vs 200MA + VIX. RISK_OFF = keine neuen Longs, Cash halten. RISK_ON = Trend-Setups bevorzugen.)_

## ATR-basierte Positionsgrößen (€{cash:.0f} Cash, {config.MAX_RISK_PER_TRADE_PERCENT}% Risiko/Trade)
{_dump(atr_sizes)}
_(Empfohlene Größe = Risiko ÷ 1.5×ATR%. Nie mehr als €{cash * config.MAX_POSITION_SIZE_PERCENT / 100:.0f}.)_
"""

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
        analysis_request += (
            f"\n\n## LAST-20 MISTAKES (Taxonomie)\n{mistake_summary}\n"
            "_Klassen: prediction (These falsch), timing (zu früh/spät/whipsaw), "
            "execution (slippage/sl_too_tight), external (news_shock/regime_shift). "
            "Wenn eine Klasse dominiert: aktiv gegensteuern (z.B. timing-heavy → "
            "Entry-Trigger strenger; execution-heavy → Spread/Vol-Gate strenger)._\n"
        )

    # Portfolio heat (sizing-critical)
    if mode in ("morning", "opening"):
        heat = compute_portfolio_heat(portfolio)
        analysis_request += (
            f"\n\n## PORTFOLIO HEAT\n{format_portfolio_heat(heat)}\n"
            "_Wenn Budget remaining < 30% vom Max: nur A+ Conv 5/5 Setups. "
            "Wenn < 10%: PASS, keine neuen Entries._\n"
        )

    # Sector exposure (cluster risk)
    if mode in ("morning", "opening"):
        sectors = compute_sector_exposure(portfolio)
        if sectors:
            analysis_request += (
                f"\n\n## SECTOR EXPOSURE\n{format_sector_exposure(sectors)}\n"
                f"_Max {config.MAX_POSITIONS_PER_SECTOR} Positionen pro Sektor. "
                f"Bei Limit: kein neuer Entry im gleichen Sektor._\n"
            )

    # Economic calendar (pre-release gating)
    if mode in ("morning", "opening"):
        macro = _today_macro_events()
        if macro:
            analysis_request += (
                f"\n\n## ⚠️ HEUTE: HIGH-IMPACT EVENTS\n{_format_macro_events(macro)}\n"
                "_Pre-Release: keine neuen Entries außer thesis ist Event-unabhängig. "
                "Offene Positionen: SL vor Event straffen oder Size halbieren._\n"
            )

    # Equity curve (morning only)
    if mode == "morning":
        eq = compute_equity_stats(portfolio.get("closed_trades", []), config.BUDGET_EUR)
        if eq:
            analysis_request += f"\n\n## EQUITY CURVE\n{format_equity_stats(eq)}\n"

    # Hit-rate self-calibration (morning only, gated at ≥3 trades)
    if mode == "morning":
        stats = compute_hit_stats(portfolio.get("closed_trades", []))
        if stats:
            if stats.get("class_suggestion"):
                logger.warning("Self-calibration: %s", stats["class_suggestion"])
            analysis_request += (
                f"\n\n## HIT-RATE (eigene History)\n{format_hit_stats(stats)}\n"
                "_Nutze zur Conviction-Kalibrierung. Brier-Line zeigt ob deine p_win-Schätzung "
                "kalibriert ist (0=perfekt, 0.25=random). KORREKTUR-Zeile: wenn aktiv, zieh den "
                "Wert von deiner nächsten p_win-Schätzung ab (Trade nur wenn p_win nach Haircut "
                "noch über 0.55 liegt). SELBST-KALIBRIERUNG-Zeile: konkrete Parameter-Anpassung "
                "aus Mistake-Klassen ableiten._\n"
            )

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
                f"\n\n## CONFLUENCE-SCORES (deterministisch, score≥{config.MIN_CONFLUENCE_SCORE}=tradeable)\n"
                + "\n".join(conf_lines) + "\n"
                "_10 Items: wk_trend_up, MA-Stack, RSI healthy, MACD bullish, Volumen, Spread tight, "
                "RS vs Index ≥0, Analyst bullish, Regime RISK_ON. Score ≥7 = full Size, 5-6 = halbe Size, <5 = PASS._\n"
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
            analysis_request += (
                f"\n\n## Earnings Kalender (nächste 3 Tage)\n{ew_lines}\n"
                "_Positionen in earnings-nahen Titeln prüfen — vor Earnings schließen oder Size reduzieren._\n"
            )

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

    # Output budgets
    if mode == "morning":
        max_tokens = 400
    elif mode == "opening":
        max_tokens = 200
    elif mode == "event":
        max_tokens = 300
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

    # Hard-stop on known Claude intro-phrases that violate the tight-output format.
    if mode in ("morning", "opening", "event"):
        create_kwargs["stop_sequences"] = [
            "Internal Analysis",
            "**Internal",
            "I'll analyze",
            "Analysiere die Daten",
            "Let me analyze",
            "**Analysis",
            "**Macro",
            "**Watch Level Review",
            "**Setup-Screen",
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
    tool_use_blocks = []

    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            name = getattr(block, "name", None)
            if name == "set_watch_levels":
                new_levels = (block.input or {}).get("levels", [])
            elif name == "recommend_entry":
                entry_recommendation = block.input or {}
            tool_use_blocks.append(block)

    # 2-Turn-Flow: tool calls without text → send tool_result back for summary.
    if tool_use_blocks and response.stop_reason == "tool_use" and not text_parts:
        tool_result_content = []
        for tb in tool_use_blocks:
            if tb.name == "set_watch_levels":
                result_text = f"Watch Levels registriert: {len(new_levels or [])} Level(s)."
            elif tb.name == "recommend_entry":
                rec_ticker = (entry_recommendation or {}).get("ticker", "?")
                result_text = f"Entry-Empfehlung für {rec_ticker} gespeichert."
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

        for block in response2.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)

    analysis_text = "\n".join(t for t in text_parts if t).strip() or "(keine Text-Analyse)"

    # Build the rec BEFORE notification so we can attach the Telegram message_id.
    rec = None
    message_id: int | None = None
    if entry_recommendation:
        # --- Risk gates: halt + edge ---
        _pf_snapshot = load_portfolio()
        _halt = risk_halt_status(_pf_snapshot)
        if _halt["halt"]:
            reason = " | ".join(_halt["reasons"])
            logger.warning("Entry BLOCKED by risk halt: %s", reason)
            _notify(f"⛔ *ENTRY BLOCKIERT* ({entry_recommendation.get('ticker','?')})\n{reason}")
            entry_recommendation = None

    if entry_recommendation and config.RISK_OFF_BLOCKS_LONGS and regime.startswith("RISK_OFF"):
        direction = str(entry_recommendation.get("direction") or "LONG").upper()
        if direction == "LONG":
            logger.warning("Entry BLOCKED by regime gate: RISK_OFF + LONG")
            _notify(
                f"⛔ *ENTRY BLOCKIERT* ({entry_recommendation.get('ticker','?')})\n"
                f"Regime={regime} → keine neuen Longs (conservative bias)."
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
            _notify(
                f"⛔ *ENTRY BLOCKIERT* ({entry_recommendation.get('ticker','?')})\n"
                f"No-Entry-Zone {_blocked_window} — Auction/EOD-Chop, schlechte Fills."
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
                _notify(
                    f"⛔ *ENTRY BLOCKIERT* ({_t})\n"
                    f"SL {_sl_dist_atr:.2f}×ATR < {config.MIN_SL_DISTANCE_ATR} → Whipsaw-Risk."
                )
                entry_recommendation = None
            elif _sl_dist_atr > config.MAX_SL_DISTANCE_ATR:
                logger.warning(
                    "Entry BLOCKED by SL-too-wide: %s SL %.2f×ATR > %.2f×ATR",
                    _t, _sl_dist_atr, config.MAX_SL_DISTANCE_ATR,
                )
                _notify(
                    f"⛔ *ENTRY BLOCKIERT* ({_t})\n"
                    f"SL {_sl_dist_atr:.2f}×ATR > {config.MAX_SL_DISTANCE_ATR} → Risk pro Trade gesprengt."
                )
                entry_recommendation = None

    if entry_recommendation:
        # Brier-Haircut: subtract calibrated bias from p_win before edge gate.
        # Why: if Claude's p_win averages 5%+ above realized win-rate, every rec
        # overstates edge. Haircut enforces what calibration text already told Claude.
        _p_raw = entry_recommendation.get("p_win")
        _stats = compute_hit_stats(_pf_snapshot.get("closed_trades", []))
        _haircut = 0.0
        if _stats and _stats.get("calibration"):
            _haircut = _stats["calibration"].get("haircut") or 0.0
        if isinstance(_p_raw, (int, float)) and _haircut > 0:
            _p_adj = max(0.01, _p_raw - _haircut)
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
            _notify(
                f"⛔ *ENTRY BLOCKIERT* ({entry_recommendation.get('ticker','?')})\n"
                f"Edge {_edge:.3f} < {config.MIN_EXPECTED_EDGE} (p·b−(1−p))"
                + (f" | p_win {_p_raw}→{_p_adj:.2f} (Brier-Haircut {_haircut:+.2f})" if _haircut > 0 else "")
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
                _notify(
                    f"⛔ *ENTRY BLOCKIERT* ({_t})\n"
                    f"Sektor `{_sector}` bereits voll: {len(_current)}/{config.MAX_POSITIONS_PER_SECTOR} "
                    f"({', '.join(_current)}). Cluster-Risiko."
                )
                entry_recommendation = None

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
            _notify(
                f"⛔ *ENTRY BLOCKIERT* ({_t})\n"
                f"Weekly-Trend DOWN — kein Long gegen primären Trend."
            )
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
                _notify(
                    f"⛔ *ENTRY BLOCKIERT* ({_t})\n"
                    f"Earnings in {_w['days_until']} Tag(en) ({_w['earnings_date']}). "
                    f"Gap-Risiko zu hoch — auf Post-Earnings-Drift warten."
                )
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
                _notify(
                    f"⛔ *ENTRY BLOCKIERT* ({_t})\n"
                    f"Relative-Strength {_rs:+.1f}pp vs Index < {config.MIN_RS_20D_VS_INDEX_PCT}pp. "
                    f"Lagger im Aufwärtstrend — kein Long. Override: setup_type=mean_reversion/reversal_oversold/gap_fill."
                )
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
                _notify(
                    f"⛔ *ENTRY BLOCKIERT* ({_t})\n"
                    f"Breakout-Setup ohne Volumen-Bestätigung (vol_ratio {_vr:.2f} < {config.MIN_BREAKOUT_VOLUME_RATIO}). "
                    f"Fake-Breakout-Risk."
                )
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
            _notify(
                f"⛔ *ENTRY BLOCKIERT* ({_t})\n"
                f"Confluence-Score {_conf['score']}/10 < {_min_conf} (setup={_setup}). "
                f"Fehlend: {', '.join(_conf.get('missing') or [])}"
            )
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
                    _notify(
                        f"⛔ *ENTRY BLOCKIERT* ({_t})\n"
                        f"Korrelation ≥{config.MAX_CORRELATION} mit {len(_high)} bestehenden Positionen: "
                        + ", ".join(f"{h}={c:.2f}" for h, c in _high.items())
                        + f". Cluster-Risk über Sektor-Cap hinaus."
                    )
                    entry_recommendation = None
                elif _corrs:
                    entry_recommendation["correlations"] = _corrs
            except Exception as e:
                logger.warning("Correlation check failed for %s: %s", _t, e)

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
        rec = {
            **entry_recommendation,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status": "pending",
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
        # Anchor message for reply-based /confirm. User replies `/confirm 3` on this post.
        message_id = _notify(
            f"🎯 *ENTRY EMPFEHLUNG: {_ticker}*\n\n"
            f"Entry: €{_entry:.2f} | SL: €{_sl:.2f}\n"
            f"TP: {_tp_str}\n"
            f"Kauf: {_shares_str} à €{_entry:.2f} = €{_actual_size:.2f} "
            f"({_pct_cap:.1f}% Kapital, Cash €{_cash:.0f})\n"
            f"Risk bei SL: €{_risk_eur:.2f} ({_risk_pct:.2f}% Kapital) | Conv: {_conv}/5\n"
            f"Hold: {_hmin}-{_hmax} Tage{_trail_line}\n\n"
            f"💡 _{_thesis}_\n\n"
            f"_Reply `/confirm` (auto={_shares_str}) oder `/confirm <stück> @<preis>` für override._"
        )
        if message_id:
            rec["message_id"] = message_id
        if MEMPALACE_AVAILABLE:
            log_trade(rec, "RECOMMENDED", rec.get("thesis", ""))
        logger.info("Entry recommendation: %s @ €%.2f (msg_id=%s)", _ticker, _entry, message_id)

    if MEMPALACE_AVAILABLE:
        log_analysis(analysis_text, mode, event_context)

    # Reload-merge save: protects concurrent writes from the Telegram listener
    # (e.g. /confirm that moves a pending_rec into open_trades).
    with portfolio_lock:
        fresh = load_portfolio()
        if new_levels is not None:
            filtered = [lvl for lvl in new_levels if lvl.get("ticker") not in excluded]
            dropped = len(new_levels) - len(filtered)
            if dropped:
                logger.warning("Dropped %d watch level(s) on excluded tickers", dropped)
            fresh["watch_levels"] = filtered
            logger.info("Watch levels updated: %d level(s) registered", len(filtered))
        if rec is not None:
            fresh.setdefault("pending_recommendations", []).append(rec)
        fresh["last_analysis"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_portfolio(fresh)

    return analysis_text
