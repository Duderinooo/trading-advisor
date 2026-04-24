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
    risk_halt_status, edge_ok,
)
from core.market_data import (
    get_market_data, get_earnings_warnings, fetch_news, market_regime,
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

## Live Kurse (mit RSI, MACD, MA20/50, BB, VWAP, Intraday-OHLC)
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
            analysis_request += (
                f"\n\n## HIT-RATE (eigene History)\n{format_hit_stats(stats)}\n"
                "_Nutze zur Conviction-Kalibrierung. Brier-Line zeigt ob deine p_win-Schätzung "
                "kalibriert ist (0=perfekt, 0.25=random). KORREKTUR-Zeile: wenn aktiv, zieh den "
                "Wert von deiner nächsten p_win-Schätzung ab (Trade nur wenn p_win nach Haircut "
                "noch über 0.55 liegt)._\n"
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

    if entry_recommendation:
        _ok, _edge = edge_ok(
            entry_recommendation.get("p_win"),
            entry_recommendation.get("entry_price"),
            entry_recommendation.get("stop_loss"),
            entry_recommendation.get("take_profit"),
        )
        if not _ok:
            logger.warning(
                "Entry BLOCKED by edge gate: edge=%.3f < %.3f (p_win=%s)",
                _edge, config.MIN_EXPECTED_EDGE, entry_recommendation.get("p_win"),
            )
            _notify(
                f"⛔ *ENTRY BLOCKIERT* ({entry_recommendation.get('ticker','?')})\n"
                f"Edge {_edge:.3f} < {config.MIN_EXPECTED_EDGE} (p·b−(1−p))"
            )
            entry_recommendation = None

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
        # Anchor message for reply-based /confirm. User replies `/confirm 3` on this post.
        message_id = _notify(
            f"🎯 *ENTRY EMPFEHLUNG: {_ticker}*\n\n"
            f"Entry: €{_entry:.2f} | SL: €{_sl:.2f}\n"
            f"TP: {_tp_str}\n"
            f"Size: €{_size:.0f} | Conviction: {_conv}/5\n"
            f"Hold: {_hmin}-{_hmax} Tage{_trail_line}\n\n"
            f"💡 _{_thesis}_\n\n"
            f"_Reply `/confirm <stück>` oder `/confirm <stück> @<preis>` zur Ausführung._"
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
