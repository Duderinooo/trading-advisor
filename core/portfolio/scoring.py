"""Setup-quality scoring: confluence (0-10) + base-quality (0-10) + format.

confluence: trend/momentum confluence — RSI / MACD / MA-stack / vol-ratio / regime / RS.
base_quality: structural-repair score for swing-low family — higher_lows / atr_contraction /
              selling_exhaustion / failed_breakdown_reclaim / tight_close / in_base_zone.
"""

import logging

import config


logger = logging.getLogger(__name__)


def compute_confluence(snap: dict, regime: str) -> dict:
    """Deterministic 0-10 confluence score for a LONG entry.
    Each item = 1 point. Aggregated score replaces gut-feel conviction.
    Returns {'score': int, 'items': dict[name, bool], 'missing': list[str]}.
    """
    if not isinstance(snap, dict) or snap.get("error") or not snap.get("price"):
        return {"score": 0, "items": {}, "missing": ["no_data"]}

    price = snap.get("price")
    ma20 = snap.get("ma20")
    ma50 = snap.get("ma50")
    rsi = snap.get("rsi14")
    macd = snap.get("macd")
    macd_sig = snap.get("macd_signal")
    vol_ratio = snap.get("volume_ratio")
    spread = snap.get("spread_pct")
    wk_trend = snap.get("wk_trend")
    rs = snap.get("rs_20d_vs_index_pct")
    rec_key = snap.get("analyst_rec_key")
    upside = snap.get("analyst_upside_pct")

    items: dict[str, bool] = {}
    items["wk_trend_up"] = (wk_trend == "UP")
    items["price_gt_ma50"] = bool(price and ma50 and price > ma50)
    items["price_gt_ma20"] = bool(price and ma20 and price > ma20)
    # 2026-05-13: rsi_healthy 40-70 → 35-75. Pullbacks zu MA20 haben oft RSI 35-40
    # (Setup-konform aber bestraft), Trending-Stocks oft RSI 70+ (Strength = Bestrafung).
    items["rsi_healthy"] = isinstance(rsi, (int, float)) and 35 <= rsi <= 75
    items["macd_bullish"] = isinstance(macd, (int, float)) and isinstance(macd_sig, (int, float)) and macd > macd_sig
    # 2026-05-13: volume_ok ≥ 1.0 → ≥ 0.8. Mid-Caps haben oft Vol-Ratio 0.6-1.0
    # ohne dass Setup kaputt ist. Breakout-spezifische Vol-Confirm läuft separat
    # über MIN_BREAKOUT_VOLUME_RATIO Gate (1.0) für setup_type=breakout_resistance.
    items["volume_ok"] = isinstance(vol_ratio, (int, float)) and vol_ratio >= 0.8
    items["spread_tight"] = isinstance(spread, (int, float)) and spread <= config.MAX_SPREAD_PERCENT / 2
    items["rs_positive"] = isinstance(rs, (int, float)) and rs >= 0
    items["analyst_bullish"] = (rec_key in ("strong_buy", "buy")) or (
        isinstance(upside, (int, float)) and upside >= 5
    )
    items["regime_risk_on"] = regime.startswith("RISK_ON") if isinstance(regime, str) else False

    score = sum(1 for v in items.values() if v)
    missing = [k for k, v in items.items() if not v]
    return {"score": score, "items": items, "missing": missing}


def compute_base_quality(snap: dict) -> dict:
    """Structural base-formation score (0-10). Complements compute_confluence
    (momentum/trend) with structure/repair signals — the swing-low family's
    primary quality indicator (manifest point 8 / 2).

    Per the manifest weighting: selling-exhaustion / atr-contraction /
    failed-breakdown-reclaim each carry +2, the rest +1, capped at 10.

    Items:
      +2 selling_exhaustion        — volume_ratio < 0.7 (proxy: dünnes Volumen = Verkäufer ausgegangen)
      +2 atr_contraction           — range_compression < 0.5 (Volatility dry-up)
      +2 failed_breakdown_reclaim  — intraday_low < ma50 < price (Verkäufer haben Support gerissen aber Recovery)
      +1 higher_lows               — higher_lows_5d ≥ 2 (Tiefs ziehen sich höher)
      +1 strong_higher_lows        — higher_lows_5d ≥ 4 (sehr robuste Folge, Bonus über higher_lows)
      +1 tight_close               — heutige Tages-Range < 0.7 × atr14_pct (Stabilisierung)
      +1 in_base_zone              — -25% ≤ pct_below_52w_high ≤ -8% (klassische Konsolidierungs-Distanz)

    Returns {"score": 0-10, "items": {name: weight_contribution}, "missing": [names with 0 contribution]}.
    """
    if not isinstance(snap, dict) or snap.get("error") or not snap.get("price"):
        return {"score": 0, "items": {}, "missing": ["no_data"]}

    price = snap.get("price")
    items: dict[str, int] = {}

    vr = snap.get("volume_ratio")
    items["selling_exhaustion"] = 2 if (isinstance(vr, (int, float)) and vr < 0.7) else 0

    rc = snap.get("range_compression")
    items["atr_contraction"] = 2 if (isinstance(rc, (int, float)) and rc < 0.5) else 0

    intraday_low = snap.get("intraday_low")
    ma50 = snap.get("ma50")
    items["failed_breakdown_reclaim"] = 2 if (
        isinstance(intraday_low, (int, float)) and isinstance(ma50, (int, float))
        and intraday_low < ma50 < price
    ) else 0

    hl = snap.get("higher_lows_5d")
    items["higher_lows"] = 1 if (isinstance(hl, (int, float)) and hl >= 2) else 0
    items["strong_higher_lows"] = 1 if (isinstance(hl, (int, float)) and hl >= 4) else 0

    day_high = snap.get("day_high")
    day_low = snap.get("day_low")
    atr_pct = snap.get("atr14_pct")
    if (isinstance(day_high, (int, float)) and isinstance(day_low, (int, float))
            and isinstance(atr_pct, (int, float)) and atr_pct > 0):
        day_range_pct = (day_high - day_low) / price * 100
        items["tight_close"] = 1 if day_range_pct < 0.7 * atr_pct else 0
    else:
        items["tight_close"] = 0

    pct_below = snap.get("pct_below_52w_high")
    items["in_base_zone"] = 1 if (
        isinstance(pct_below, (int, float)) and -25 <= pct_below <= -8
    ) else 0

    score = min(10, sum(items.values()))
    missing = [k for k, w in items.items() if w == 0]
    return {"score": score, "items": items, "missing": missing}


def format_confluence(c: dict) -> str:
    if not c or "score" not in c:
        return ""
    score = c["score"]
    items = c.get("items") or {}
    hit = [k for k, v in items.items() if v]
    miss = [k for k, v in items.items() if not v]
    return (
        f"Confluence {score}/10 — ✓ "
        + (", ".join(hit) if hit else "—")
        + (f" | ✗ {', '.join(miss)}" if miss else "")
    )


# ---------- Correlation gate ----------
