"""Prompt assembly: per-mode config + user-message composition.

MODE_CONFIG holds declarative settings per mode (system prompt, suffix, tools,
max_tokens, model, force_any_tool). build_user_message composes the user message
via sections.append + "\\n\\n".join.
"""

import logging
import re
from datetime import datetime

import config
from memory import build_history_context, MEMPALACE_AVAILABLE
from macro import today_events as _today_macro_events, format_events as _format_macro_events

from core.data.market_data import get_earnings_warnings, fetch_news
from core.portfolio import (
    compute_hit_stats, format_hit_stats,
    compute_portfolio_heat, format_portfolio_heat,
    compute_sector_exposure, format_sector_exposure,
    compute_equity_stats, format_equity_stats,
    compute_confluence,
)
from core.llm.prompt.prompts import (
    MORNING_PREP_PROMPT, OPENING_CHECK_PROMPT, EVENT_TRIGGER_PROMPT,
    WATCH_LEVELS_TOOL, RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
    UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL, SUBMIT_PASS_TOOL,
)
from core.llm.prompt.context import RequestContext
from core.llm.prompt.thesis_diff import (
    build_stale_horizon_lines,
    build_thesis_degradation_lines,
)
from core.llm.serialization import dump


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-mode prompt suffixes (tool-usage instructions)
# ---------------------------------------------------------------------------

_MORNING_SUFFIX = (
    "\n\nWICHTIG: Rufe IMMER `set_watch_levels` auf (auch mit leerer Liste — empty=behält bestehende). "
    "Bei A+-Setup mit Conv ≥3/5: AUCH `recommend_entry` aufrufen. "
    "Bei verstärkter These bestehender Position: `recommend_add_to_position`. "
    "Pro offene Position: prüfe ob SL/TP noch passen → `update_position_targets` "
    "(z.B. neuer Resistance, Goldman-target raise). "
    "Bei Thesis-Bruch / Earnings-Defense: `recommend_exit`."
)
_OPENING_SUFFIX = (
    "\n\nOUTPUT-FORMAT: AUSSCHLIESSLICH via tool_use, KEINE Prosa. Wenn keine "
    "Action passt → `submit_pass` mit 1-Satz-Reason. Spart Tokens + verhindert "
    "lange Texte ohne Decision."
)
_EVENT_SUFFIX = (
    "\n\nBei ENTRY-Empfehlung mit Conv ≥3/5: `recommend_entry`. "
    "Bei verstärkter These offener Position: `recommend_add_to_position`. "
    "Bei SL/TP-Anpassung wegen Catalyst: `update_position_targets`. "
    "Bei Thesis-Bruch: `recommend_exit`. "
    "Wenn keine Action passt: `submit_pass` mit 1-Satz-Reason. "
    "OUTPUT: AUSSCHLIESSLICH via tool_use, KEINE Prosa (spart Tokens)."
)

# Per-mode tool budget. See research/2026-04-29-watch-tool-truncation.md for
# the bump history (400 → 3500 morning, 500 → 900 opening/event).
MODE_CONFIG: dict[str, dict] = {
    "morning": {
        "base_prompt": MORNING_PREP_PROMPT,
        "suffix": _MORNING_SUFFIX,
        "tools": [
            WATCH_LEVELS_TOOL, RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
            UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL,
        ],
        "max_tokens": 3500,
        "force_any_tool": True,
        "model": config.CLAUDE_MODEL_MORNING,
    },
    "opening": {
        "base_prompt": OPENING_CHECK_PROMPT,
        "suffix": _OPENING_SUFFIX,
        "tools": [
            RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
            UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL, SUBMIT_PASS_TOOL,
        ],
        # 2026-06-10: opening → Sonnet. Haiku diverged repeatedly on opening
        # tool-calls (error_max_structured_output_retries, 3/3 retry-cap on
        # US-open). Sonnet handles the 5-tool force-any reliably. Budget 900→1500
        # for headroom (tight budget was a divergence suspect).
        "max_tokens": 1500,
        "force_any_tool": True,
        "model": config.CLAUDE_MODEL_MORNING,
    },
    "event": {
        "base_prompt": EVENT_TRIGGER_PROMPT,
        "suffix": _EVENT_SUFFIX,
        # Event-mode darf set_watch_levels nicht überschreiben (Morning-Domain).
        "tools": [
            RECOMMEND_ENTRY_TOOL, RECOMMEND_ADD_TOOL,
            UPDATE_TARGETS_TOOL, RECOMMEND_EXIT_TOOL, SUBMIT_PASS_TOOL,
        ],
        "max_tokens": 900,
        "force_any_tool": True,
        "model": config.CLAUDE_MODEL_EVENT,
    },
    "standard": {
        "base_prompt": "",
        "suffix": "",
        "tools": None,
        "max_tokens": 400,
        "force_any_tool": False,
        "model": config.CLAUDE_MODEL_EVENT,
    },
}


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def _context_intro(mode: str, event_context: str | None) -> str:
    if mode == "morning":
        return "MORNING"
    if mode == "opening":
        return f"OPEN-CHECK {event_context or ''}".strip()
    if mode == "event":
        return f"🚨 EVENT: {event_context}"
    return "Standard-Analyse"


def _slim_market_ctx(market_ctx: dict) -> dict:
    """Trim market_ctx to per-ticker essentials (saves ~80% of those tokens)."""
    out: dict = {}
    for _t, _d in market_ctx.items():
        if not isinstance(_d, dict) or _d.get("error"):
            continue
        if _t == "SPY5.DE":
            keep = ("price", "prev_close", "change_pct", "ma50", "ma200", "rsi14")
        elif _t == "EQQQ.DE":
            keep = ("price", "change_pct", "rsi14")
        elif _t == "^VIX":
            keep = ("price", "change_pct")
        else:
            keep = ("price", "change_pct")
        out[_t] = {k: _d.get(k) for k in keep if _d.get(k) is not None}
    return out


def _history_context_section(ctx: RequestContext) -> str:
    if not MEMPALACE_AVAILABLE:
        return ""
    primary_ticker = None
    if ctx.mode == "event" and ctx.event_context:
        m = re.search(r"\b([A-Z0-9]{1,6}(?:\.[A-Z]{1,3})?)\b", ctx.event_context)
        if m:
            primary_ticker = m.group(1)
    if not primary_ticker and ctx.open_trade_tickers:
        primary_ticker = ctx.open_trade_tickers[0]
    return build_history_context(
        ticker=primary_ticker,
        query=ctx.event_context if ctx.mode == "event" else None,
    )


def _mistake_summary(portfolio: dict) -> str:
    """Class + tag distribution over last 20 losses."""
    closed = portfolio.get("closed_trades", [])
    losses = [t for t in closed if (t.get("pnl_pct") or 0) <= 0][-20:]
    if not losses:
        return ""
    by_class: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    for t in losses:
        cls = t.get("mistake_class") or "untagged"
        tag = t.get("mistake_tag") or "untagged"
        by_class[cls] = by_class.get(cls, 0) + 1
        by_tag[tag] = by_tag.get(tag, 0) + 1
    top_cls = sorted(by_class.items(), key=lambda x: -x[1])
    top_tag = sorted(by_tag.items(), key=lambda x: -x[1])[:5]
    return (
        f"Letzte {len(losses)} Losses nach Klasse: "
        + ", ".join(f"{c}={n}" for c, n in top_cls)
        + " | Top-Tags: " + ", ".join(f"{t}={n}" for t, n in top_tag)
    )


def _haircut_directive(stats: dict | None) -> str:
    """Inverse-prompt so Haiku doesn't blindly mirror historical bias.
    Bug 2026-05-07: haircut=-0.38 silently killed CON.DE +9% entry."""
    cal = (stats or {}).get("calibration") or {}
    hc = cal.get("haircut")
    if not hc:
        return ""
    if hc < 0:
        return (
            f"Bot war historisch ZU PESSIMISTISCH (haircut={hc:+.2f}, edge gate "
            f"addiert {abs(min(0.20, abs(hc))):+.2f} auto auf dein p_win) — sei AGGRESSIVER"
        )
    return (
        f"Bot war historisch ZU OPTIMISTISCH (haircut={hc:+.2f}, edge gate "
        f"zieht {min(0.20, abs(hc)):.2f} auto von deinem p_win ab) — sei STRENGER"
    )


def _news_tickers_for_mode(ctx: RequestContext) -> list[str]:
    if ctx.mode == "event":
        return list({w["ticker"] for w in ctx.portfolio.get("watch_levels", [])})[:5]
    if ctx.mode == "morning":
        open_t = [t["ticker"] for t in ctx.portfolio.get("open_trades", [])]
        return list(dict.fromkeys(open_t + config.WATCHLIST))[:5]
    return []


# ---------------------------------------------------------------------------
# Public assembly
# ---------------------------------------------------------------------------

def build_user_message(ctx: RequestContext) -> str:
    """Compose the user message. Mandatory base section first, then optional
    mode-gated section blocks. Joined with blank-line separators."""
    intro = _context_intro(ctx.mode, ctx.event_context)
    pf = ctx.portfolio

    sections: list[str] = []
    sections.append(
        f"{intro}\n\n"
        f"Datum/Zeit: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## Portfolio\n"
        f"Kapital: €{pf.get('total_capital_eur', config.BUDGET_EUR):.2f}\n"
        f"Cash: €{pf.get('cash_eur', config.BUDGET_EUR):.2f}\n\n"
        f"## Offene Positionen\n"
        f"{dump(pf.get('open_trades', [])) if pf.get('open_trades') else 'Keine'}\n\n"
        f"## Aktive Watch Levels\n"
        f"{dump(pf.get('watch_levels', [])) if pf.get('watch_levels') else 'Keine definiert'}\n\n"
        f"## Markt-Kontext (Indizes + VIX)\n"
        f"{dump(_slim_market_ctx(ctx.market_ctx))}\n\n"
        f"## Live Kurse (mit RSI, MACD, MA20/50, BB, VWAP, Intraday-OHLC, Analyst-Konsens, insider_signals_30d)\n"
        f"{dump(ctx.market_data)}\n\n"
        f"## Markt-Regime\n{ctx.regime}\n\n"
        f"## ATR-basierte Positionsgrößen (€{ctx.cash:.0f} Cash, {config.MAX_RISK_PER_TRADE_PERCENT}% Risiko/Trade)\n"
        f"{dump(ctx.atr_sizes)}"
    )

    # No-entry-window awareness: the no_entry_zone gate (analyzer step 3) blocks
    # every recommend_entry during auction/EOD chop windows. Telling Claude up
    # front stops it burning output tokens on entries guaranteed to be rejected
    # (2026-06-10 review: 80 no_entry_zone blocks — the US-open check at 15:35
    # sits inside the 15:30–15:40 window, so its entries never pass the gate).
    from core.events.dedup import in_no_entry_window
    if in_no_entry_window(datetime.now()):
        sections.append(
            "## ⛔ NO-ENTRY-FENSTER AKTIV\n"
            "Auktions-/EOD-Chop-Fenster — neue Entries werden deterministisch "
            "geblockt (schlechte Fills). KEINE recommend_entry emittieren. Nur "
            "Bestand verwalten: recommend_exit, update_position_targets, sonst "
            "submit_pass."
        )

    # Thesis-degradation (all modes if open trades exist).
    degr_lines = build_thesis_degradation_lines(
        pf.get("open_trades", []), ctx.market_data,
    )
    if degr_lines:
        sections.append("## THESIS-STATUS (Snapshot vs. Jetzt)\n" + "\n".join(degr_lines))

    # Zeit-Horizont: positions past hold_days_max. Replaces the old mechanical
    # 🕒 STALE-THESIS Telegram nag — hand overdue positions to Claude for an
    # explicit hold-vs-exit verdict instead of pinging the user to go check.
    # Scoped to the deliberate Sonnet passes (morning/opening); horizon is a
    # daily-granularity concept, so re-judging on every Haiku event adds noise.
    if ctx.mode in ("morning", "opening"):
        stale_lines = build_stale_horizon_lines(pf.get("open_trades", []))
        if stale_lines:
            sections.append(
                "## ⏰ ZEIT-HORIZONT ÜBERSCHRITTEN\n"
                "Diese Positionen sind über ihrem geplanten Hold-Horizont. KEIN "
                "Auto-Verkauf — beurteile JEDE explizit: Thesis intakt + "
                "Momentum/Trail trägt → halten (submit_pass). Stagnation / "
                "Thesis-Decay (vgl. THESIS-STATUS) → recommend_exit.\n"
                + "\n".join(stale_lines)
            )

    # Gaps (morning + opening).
    if ctx.mode in ("morning", "opening"):
        gap_lines: list[str] = []
        watched = set(ctx.open_trade_tickers) | {w["ticker"] for w in pf.get("watch_levels", [])}
        for t in watched:
            d = ctx.market_data.get(t, {})
            pct = d.get("change_pct")
            if pct is not None and abs(pct) >= config.GAP_FLAG_PERCENT:
                tag = "POS" if t in ctx.open_trade_tickers else "WATCH"
                price = d.get("price")
                price_str = f" @ €{price:.2f}" if price else ""
                gap_lines.append(f"  ⚠️ [{tag}] {t}: {pct:+.1f}%{price_str}")
        if gap_lines:
            sections.append(
                f"## GAPS (≥{config.GAP_FLAG_PERCENT}% vs prev close)\n" + "\n".join(gap_lines)
            )

    # MemPalace history.
    history = _history_context_section(ctx)
    if history:
        sections.append(f"## Relevante History (aus MemPalace)\n{history}")

    # Mistake summary.
    mistake = _mistake_summary(pf)
    if mistake:
        sections.append(f"## LAST-20 MISTAKES (Taxonomie)\n{mistake}")

    # Heat / sectors / macro (morning + opening).
    if ctx.mode in ("morning", "opening"):
        heat = compute_portfolio_heat(pf)
        sections.append(f"## PORTFOLIO HEAT\n{format_portfolio_heat(heat)}")
        sectors = compute_sector_exposure(pf)
        if sectors:
            sections.append(f"## SECTOR EXPOSURE\n{format_sector_exposure(sectors)}")
        macro = _today_macro_events()
        if macro:
            sections.append(f"## ⚠️ HEUTE: HIGH-IMPACT EVENTS\n{_format_macro_events(macro)}")

    # Equity curve + hit-rate (morning) / haircut-directive (event/opening).
    if ctx.mode == "morning":
        eq = compute_equity_stats(
            pf.get("closed_trades", []), config.BUDGET_EUR, pf.get("cash_movements", []),
        )
        if eq:
            sections.append(f"## EQUITY CURVE\n{format_equity_stats(eq)}")
        stats = compute_hit_stats(pf.get("closed_trades", []), pf.get("cash_movements", []))
        if stats:
            if stats.get("class_suggestion"):
                logger.warning("Self-calibration: %s", stats["class_suggestion"])
            sections.append(f"## HIT-RATE (eigene History)\n{format_hit_stats(stats)}")
    elif ctx.mode in ("event", "opening"):
        stats = compute_hit_stats(pf.get("closed_trades", []), pf.get("cash_movements", []))
        haircut_text = _haircut_directive(stats)
        if haircut_text:
            sections.append(f"## BRIER-CAL\n{haircut_text}")

    # Confluence scores (morning + opening + event).
    if ctx.mode in ("morning", "opening", "event"):
        conf_lines: list[str] = []
        for _t, _d in ctx.market_data.items():
            if not isinstance(_d, dict) or _d.get("error"):
                continue
            if _t in config.MARKET_INDICATORS or _t in config.COMMODITIES:
                continue
            c = compute_confluence(_d, ctx.regime)
            if c["score"] >= 4:  # only non-trivial scores
                hits = [k for k, v in c["items"].items() if v]
                conf_lines.append(f"  {_t}: {c['score']}/10 — {', '.join(hits)}")
        if conf_lines:
            sections.append("## CONFLUENCE-SCORES\n" + "\n".join(conf_lines))

    # Earnings calendar (morning).
    if ctx.mode == "morning":
        earnings_tickers = (
            [t["ticker"] for t in pf.get("open_trades", [])]
            + config.WATCHLIST + config.COMMODITIES
        )
        ew = get_earnings_warnings(list(set(earnings_tickers)), days_ahead=3)
        if ew:
            ew_lines = "\n".join(
                f"  ⚠️ {w['ticker']}: Earnings in {w['days_until']} Tag(en) ({w['earnings_date']})"
                for w in ew
            )
            sections.append(f"## Earnings Kalender (nächste 3 Tage)\n{ew_lines}")

    # News headlines (morning + event).
    news_tickers = _news_tickers_for_mode(ctx)
    if news_tickers:
        news = fetch_news(news_tickers, limit_per_ticker=3)
        if news:
            news_lines = "\n".join(
                f"  {t}: " + " | ".join(headlines)
                for t, headlines in news.items()
            )
            sections.append(f"## Aktuelle News\n{news_lines}")

    return "\n\n".join(sections)
