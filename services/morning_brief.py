"""Morning brief service: prep checks + deterministic render.

LLM = decision engine via tool-calls. Backend = presentation.
Sonnet's text output is discarded; this module renders Telegram from portfolio state.
"""

import logging
from datetime import datetime

import config
from core import (
    analyze_portfolio, load_portfolio, save_portfolio,
    get_earnings_warnings, get_dividend_warnings,
    kill_switch_active, get_market_data,
    compute_portfolio_heat, compute_sector_exposure,
)
from notifier import send_alert, send_daily_summary

from runtime.scheduler import (
    morning_prep_done_today, mark_morning_prep_done,
    is_transient_error, transient_retry_inc, transient_retry_reset,
    TRANSIENT_RETRY_CAP,
)
from services.risk_guards import check_stale_theses


logger = logging.getLogger("trading_advisor.morning_brief")


def _render_morning_brief() -> str:
    """Deterministic morning Telegram aus portfolio state + live market."""
    pf = load_portfolio()
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        indices = get_market_data(list(config.MARKET_INDICATORS))
    except Exception:
        indices = {}
    spy = indices.get("SPY5.DE") or indices.get("SPY") or {}
    vix = indices.get("^VIX") or {}
    spy_p, spy_ma200, spy_rsi = spy.get("price"), spy.get("ma200"), spy.get("rsi14")
    vix_p = vix.get("price")

    if isinstance(spy_p, (int, float)) and isinstance(spy_ma200, (int, float)):
        regime = "RISK_ON" if spy_p > spy_ma200 else "RISK_OFF"
        cmp = ">" if spy_p > spy_ma200 else "<"
        regime_line = f"📊 *Markt:* SPY €{spy_p:.2f} {cmp} MA200 = {regime}"
        if isinstance(spy_rsi, (int, float)):
            tag = " überkauft" if spy_rsi > 70 else (" oversold" if spy_rsi < 30 else "")
            regime_line += f" (RSI {spy_rsi:.0f}{tag})"
        if isinstance(vix_p, (int, float)):
            vtag = "calm" if vix_p < 20 else ("elevated" if vix_p < 30 else "spike")
            regime_line += f" · VIX {vix_p:.1f} {vtag}"
    else:
        regime_line = "📊 *Markt:* Daten n/a"

    pending = pf.get("pending_recommendations", []) or []
    entry_recs = [
        r for r in pending
        if r.get("kind", "entry") in ("entry", None)
        and (r.get("timestamp", "") or "").startswith(today)
    ]

    def _rr_of(r: dict) -> float:
        ep = float(r.get("entry_price") or 0)
        sl = float(r.get("stop_loss") or 0)
        tp = r.get("take_profit") or []
        if not isinstance(tp, list):
            tp = [tp]
        if ep <= 0 or sl <= 0 or ep <= sl or not tp:
            return 0.0
        risk = ep - sl
        try:
            reward = float(tp[0]) - ep
        except (TypeError, ValueError):
            return 0.0
        return reward / risk if risk > 0 else 0.0

    def _quality_of(r: dict) -> float:
        return float(r.get("base_quality_at_entry") or r.get("confluence_at_entry") or 0)

    entry_recs.sort(
        key=lambda r: (
            int(r.get("conviction") or 0),
            _rr_of(r),
            _quality_of(r),
        ),
        reverse=True,
    )

    open_trades = pf.get("open_trades", []) or []
    watch_levels = pf.get("watch_levels", []) or []

    n_analyzed = len(set(config.WATCHLIST))
    if entry_recs:
        summary = f"🔍 {n_analyzed} Ticker analysiert. {len(entry_recs)} Limit-Buy-Kandidat(en):"
    else:
        summary = f"🔍 {n_analyzed} Ticker analysiert. Keine sauberen Limit-Buy-Kandidaten heute."

    entry_lines = []
    for r in entry_recs:
        t = r.get("ticker", "?")
        ep = float(r.get("entry_price") or 0)
        sl = float(r.get("stop_loss") or 0)
        tp = r.get("take_profit") or []
        size = float(r.get("size_eur") or 0)
        conv = r.get("conviction") or 0
        signal = r.get("primary_signal") or r.get("thesis", "")
        tp_list = tp if isinstance(tp, list) else [tp]
        tp_str = "/".join(f"€{float(x):.2f}" for x in tp_list if x)
        line = f"💎 *{t}* | Entry €{ep:.2f} | SL €{sl:.2f} | TP {tp_str} | Size €{size:.0f} | Conv {conv}/5"
        if signal:
            line += f"\n  _{signal[:100]}_"
        entry_lines.append(line)

    open_lines = []
    if open_trades:
        try:
            live = get_market_data([t.get("ticker") for t in open_trades if t.get("ticker")])
        except Exception:
            live = {}
        for t in open_trades:
            tk = t.get("ticker", "?")
            ep = float(t.get("entry_price") or 0)
            sl = float(t.get("stop_loss") or 0)
            tp = t.get("take_profit") or []
            cur = float(live.get(tk, {}).get("price", ep) or ep)
            chg_pct = ((cur - ep) / ep * 100) if ep > 0 else 0
            tp_list = tp if isinstance(tp, list) else [tp]
            tp_str = "/".join(f"€{float(x):.2f}" for x in tp_list if x)
            open_lines.append(
                f"📌 *{tk}* | €{cur:.2f} ({chg_pct:+.1f}%) | SL €{sl:.2f} TP {tp_str} | HALTEN"
            )

    parts = [regime_line, summary]
    if entry_lines:
        parts.append("")
        parts.extend(entry_lines)
    if open_lines:
        parts.append("")
        parts.append("*Offene Positionen:*")
        parts.extend(open_lines)
    if watch_levels:
        defense_only = [w for w in watch_levels if w.get("invalidate_below")]
        if defense_only:
            parts.append("")
            parts.append(f"🛡️ {len(defense_only)} Defense-Watch(es) aktiv")

    # Risk-Block.
    try:
        heat = compute_portfolio_heat(pf)
        capital = float(pf.get("total_capital_eur", config.BUDGET_EUR) or config.BUDGET_EUR)
        cash = float(pf.get("cash_eur", 0) or 0)
        cash_pct = (cash / capital * 100) if capital > 0 else 0
        sectors_map = compute_sector_exposure(pf)
        sectors_str = ", ".join(
            f"{s}×{len(ts)}" for s, ts in sorted(sectors_map.items(), key=lambda x: -len(x[1]))
        ) if sectors_map else "—"
        risk_lines = [
            "",
            "📉 *Risk:* "
            f"Heat {heat['heat_pct']:.1f}% · Cash {cash_pct:.0f}% · Sektoren: {sectors_str}",
        ]
        open_tickers = [t.get("ticker") for t in open_trades if t.get("ticker")]
        if open_tickers:
            try:
                er = get_earnings_warnings(open_tickers, days_ahead=14)
                if er:
                    nearest = er[0]
                    risk_lines[-1] += (
                        f" · ⚠️ Earnings {nearest['ticker']} T-{nearest['days_until']}"
                    )
            except Exception:
                pass
        parts.extend(risk_lines)
    except Exception:
        logger.exception("morning_brief risk-block failed")

    return "\n".join(parts)


def _earnings_pre_check() -> None:
    """Direct earnings alert for open positions — fires before Claude."""
    try:
        open_tickers = [t["ticker"] for t in load_portfolio().get("open_trades", [])]
        if not open_tickers:
            return
        for w in get_earnings_warnings(open_tickers, days_ahead=2):
            if w["days_until"] <= config.EARNINGS_CLOSE_DAYS:
                send_alert(
                    f"🚨 EARNINGS MORGEN: {w['ticker']} — CLOSE EMPFOHLEN",
                    f"Earnings in *{w['days_until']} Tag(en)* ({w['earnings_date']})\n"
                    f"⚠️ Position JETZT schließen oder auf 25% Size reduzieren.\n"
                    f"Grund: Overnight-Gap-Risiko (IV crush + unbekannte Richtung).",
                )
                logger.warning("Earnings CLOSE recommended: %s in %d days", w["ticker"], w["days_until"])
            else:
                send_alert(
                    f"⚠️ EARNINGS: {w['ticker']}",
                    f"Earnings in *{w['days_until']} Tag(en)* ({w['earnings_date']})\n"
                    f"Offene Position! Vor Earnings schließen oder Size reduzieren.",
                )
                logger.warning("Earnings warning sent: %s in %d days", w["ticker"], w["days_until"])
    except Exception:
        logger.exception("Earnings pre-check failed")


def _dividend_pre_check() -> None:
    """Ex-div warning only when mechanical drop threatens SL."""
    try:
        open_trades = load_portfolio().get("open_trades", [])
        if not open_trades:
            return
        by_ticker = {t["ticker"]: t for t in open_trades}
        warns = get_dividend_warnings(list(by_ticker.keys()), days_ahead=config.DIVIDEND_WARN_DAYS)
        for w in warns:
            trade = by_ticker.get(w["ticker"])
            if not trade:
                continue
            entry = trade.get("entry_price")
            sl = trade.get("stop_loss")
            div = w["expected_div"]
            if not (entry and sl and entry > sl):
                logger.info("Dividend info-only (no SL): %s ex=%s div=%.2f", w["ticker"], w["ex_date"], div)
                continue
            sl_distance = entry - sl
            drop_share = div / sl_distance if sl_distance > 0 else 0
            if drop_share < 0.5:
                logger.info(
                    "Dividend info-only (drop_share=%.2f<0.5): %s ex=%s div=%.2f",
                    drop_share, w["ticker"], w["ex_date"], div,
                )
                continue
            suggested_sl = round(sl - div, 2)
            msg = (
                f"Ex-Div in *{w['days_until']} Tag(en)* ({w['ex_date']})\n"
                f"Erwartete Ausschüttung: ~€{div:.2f}/Aktie\n"
                f"SL-Distance: €{sl_distance:.2f} | Ex-Div-Drop frisst {drop_share*100:.0f}% davon\n"
                f"⚠️ Mechanischer Drop kann SL triggern.\n"
                f"Vorschlag: SL temporär auf €{suggested_sl:.2f} senken (heute Abend), "
                f"nach Ex-Div ({w['ex_date']}) zurücksetzen."
            )
            send_alert(f"💸 EX-DIV WARNUNG: {w['ticker']} — SL-Risiko", msg)
            logger.warning(
                "Dividend SL-threat alert: %s ex=%s div=%.2f drop_share=%.2f",
                w["ticker"], w["ex_date"], div, drop_share,
            )
    except Exception:
        logger.exception("Dividend pre-check failed")


def run_morning_prep(force: bool = False) -> None:
    """Run morning analysis. force=True overrides date-dedup + kill-switch."""
    if not force and morning_prep_done_today():
        return
    if not force and kill_switch_active(load_portfolio()):
        logger.info("Morning prep skipped: kill-switch active")
        mark_morning_prep_done()
        return

    logger.info("☀️ Running morning prep%s...", " (forced)" if force else "")

    try:
        check_stale_theses()
    except Exception:
        logger.exception("Stale-thesis check failed")

    _earnings_pre_check()
    _dividend_pre_check()

    try:
        analysis = analyze_portfolio(
            mode="morning", force=force, bypass_cooldown=force
        )
        if analysis and analysis.startswith("⚠️ Analysis skipped"):
            logger.info("Morning prep deferred: %s", analysis)
            return

        # LLM text discarded. Render Telegram deterministic.
        pf = load_portfolio()
        tr = pf.get("last_morning_trace") or {}
        tool_called = bool(tr.get("tool_called"))
        sonnet_failed = (
            not tool_called
            and (tr.get("output_tokens") or 0) < 20
        )
        if sonnet_failed:
            send_alert(
                "🚨 MORNING FAIL — Sonnet schwieg",
                f"Sonnet emittierte {tr.get('output_tokens')} tokens, "
                f"stop={tr.get('stop_reason')}, kein tool_use. "
                f"Bot ist heute BLIND. Manuell: /morning erneut.",
            )
            logger.error(
                "Morning brief: Sonnet returned empty (out_tok=%s, stop=%s)",
                tr.get("output_tokens"), tr.get("stop_reason"),
            )
            return

        send_daily_summary(_render_morning_brief())
        logger.info("✅ Morning prep sent (deterministic render)")
        transient_retry_reset("morning")
        mark_morning_prep_done()
    except Exception as e:
        if is_transient_error(e):
            n = transient_retry_inc("morning")
            logger.warning(
                "Morning prep transient error (%d/%d): %s",
                n, TRANSIENT_RETRY_CAP, e,
            )
            if n >= TRANSIENT_RETRY_CAP:
                send_alert(
                    "Morning Prep Error",
                    f"{e}\n\n{n} transiente Versuche fehlgeschlagen — aufgegeben. Manuell: /morning",
                )
                mark_morning_prep_done()
        else:
            logger.exception("Morning prep failed (persistent)")
            send_alert("Morning Prep Error", f"{e}\n\nKein Auto-Retry. Manuell: /morning")
            mark_morning_prep_done()
