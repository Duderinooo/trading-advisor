"""Thesis-degradation diff: per-position snapshot-vs-current pillar breakage.

Only surfaces DEGRADATIONS — improvements are noise for the LLM. Used by
prompt_builder to inject THESIS-STATUS section so Claude can act on broken
pillars (analyst flip bearish, MA50 loss, wk_trend down, RS deterioration,
upside collapse) instead of letting positions silently rot.
"""

# Lower index = more bullish. analyst_rec_key downgrade = rank-increase ≥1.
_REC_KEY_RANK = {
    "strong_buy": 0, "buy": 1, "outperform": 1,
    "hold": 2, "neutral": 2,
    "underperform": 3, "sell": 4, "strong_sell": 4,
}


def build_thesis_degradation_lines(open_trades: list, market_data: dict) -> list[str]:
    """Per-position diff between frozen entry_snapshot and current market_data.
    Emits one line per ticker with DOWNGRADE / STRUCTURAL_BREAK flags."""
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
