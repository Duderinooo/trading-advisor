"""Request context: load portfolio + market data, run pre-filters, classify state.

Owns the liquidity / whole-share / entry-gate-cooldown pre-filter pipeline that
runs BEFORE Claude sees the data, plus the categorical state classifier annotation.
"""

import logging
from dataclasses import dataclass, field

import config
from core.portfolio import (
    portfolio_lock, load_portfolio, save_portfolio, suggest_position_size,
    maintain_drawdown_state,
    max_affordable_share_price_eur,
    active_entry_gate_cooldowns,
)
from core.data.market_data import get_market_data, market_regime
from core.llm.prompt.state_classifier import classify_state


logger = logging.getLogger(__name__)


@dataclass
class RequestContext:
    """All state shared between prompt-build / dispatch / persist phases."""
    mode: str
    event_context: str | None
    portfolio: dict
    market_data: dict
    market_ctx: dict
    regime: str
    cash: float
    open_trade_tickers: list[str]
    watch_level_tickers: list[str]
    excluded: set[str]
    atr_sizes: dict[str, float] = field(default_factory=dict)


def build_request_context(mode: str, event_context: str | None) -> RequestContext:
    """Load portfolio, fetch market data, run pre-filters, classify state."""
    # DD-state hysteresis maintenance before any read.
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

    market_data = get_market_data(tradeable)
    market_ctx = get_market_data(list(config.MARKET_INDICATORS))

    market_data = _apply_liquidity_filter(
        market_data, mode, set(open_trade_tickers) | set(watch_level_tickers),
        max_affordable_share_price_eur(portfolio),
    )
    _annotate_entry_cooldowns(market_data, portfolio)

    regime = market_regime(market_ctx)
    cash = portfolio.get("cash_eur", config.BUDGET_EUR)
    atr_sizes = {
        t: suggest_position_size(market_data[t].get("atr14_pct"), cash)
        for t in tradeable if t in market_data and not market_data[t].get("error")
    }

    # Python classifies categorical state per ticker; LLM references it
    # instead of re-deriving from raw indicators.
    for _t, _d in market_data.items():
        if isinstance(_d, dict) and not _d.get("error"):
            _d["state"] = classify_state(_d, regime)

    return RequestContext(
        mode=mode,
        event_context=event_context,
        portfolio=portfolio,
        market_data=market_data,
        market_ctx=market_ctx,
        regime=regime,
        cash=cash,
        open_trade_tickers=open_trade_tickers,
        watch_level_tickers=watch_level_tickers,
        excluded=excluded,
        atr_sizes=atr_sizes,
    )


def _apply_liquidity_filter(
    market_data: dict, mode: str, protected: set[str], max_share_price: float,
) -> dict:
    """Drop illiquid / wide-spread / unaffordable tickers before Claude sees them.
    Protected set (open + watch tickers) always stays — exit + breakout context
    needs them, watch-levels were set for a reason."""
    kept = {}
    dropped_illiquid: list[str] = []
    dropped_unaffordable: list[str] = []
    gate_vol = config.MIN_VOLUME_RATIO_OPENING if mode == "opening" else config.MIN_VOLUME_RATIO
    for _t, _d in market_data.items():
        if not isinstance(_d, dict) or _d.get("error"):
            kept[_t] = _d
            continue
        if _t in protected:
            kept[_t] = _d
            continue
        vr = _d.get("volume_ratio")
        sp = _d.get("spread_pct")
        pr = _d.get("price")
        # vol_ratio==0 = no aggregated daily volume yet (pre-open / post-weekend).
        # Treat as no-data; only gate positive-but-thin values.
        if isinstance(vr, (int, float)) and 0 < vr < gate_vol:
            dropped_illiquid.append(f"{_t}(vol_ratio={vr})")
            continue
        if sp is not None and sp > config.MAX_SPREAD_PERCENT:
            dropped_illiquid.append(f"{_t}(spread={sp}%)")
            continue
        if isinstance(pr, (int, float)) and pr > max_share_price:
            dropped_unaffordable.append(f"{_t}(price=€{pr:.2f}>€{max_share_price:.2f})")
            continue
        kept[_t] = _d
    if dropped_illiquid:
        logger.info("Liquidity gate dropped (mode=%s, vol_min=%.2f): %s",
                    mode, gate_vol, ", ".join(dropped_illiquid))
    if dropped_unaffordable:
        logger.info("Whole-share gate dropped (cap=€%.2f): %s",
                    max_share_price, ", ".join(dropped_unaffordable))
    return kept


def _annotate_entry_cooldowns(market_data: dict, portfolio: dict) -> None:
    """Stamp `entry_cooldown` marker on tickers that recently failed RS / edge /
    red-team gates. Claude sees the marker and skips re-recommending. Hard gates
    still re-check live data downstream — a genuine improvement is never missed."""
    cooldowns = active_entry_gate_cooldowns(portfolio)
    for _ct, _cd in cooldowns.items():
        _cdata = market_data.get(_ct)
        if isinstance(_cdata, dict) and not _cdata.get("error"):
            _cdata["entry_cooldown"] = (
                f"{_cd.get('gate')}: {_cd.get('reason')} — KEIN recommend_entry, "
                f"Gate würde ohnehin blocken (Cooldown bis "
                f"{config.ENTRY_GATE_COOLDOWN_MIN}min nach Fail)"
            )
