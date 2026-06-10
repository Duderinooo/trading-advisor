"""Public API for the core trading logic.

Re-exports the common surface so callers can write:
    from core import analyze_portfolio, check_price_alerts, ...

Internals are still accessible via their submodules (core.portfolio, ...).
"""

from core.portfolio import (
    portfolio_lock,
    paper_lock,
    load_portfolio,
    save_portfolio,
    load_paper_portfolio,
    save_paper_portfolio,
    build_trade_dict,
    add_cash_movement,
    suggest_position_size,
    compute_kelly_mult,
    compute_slippage_budget,
    compute_hit_stats,
    format_hit_stats,
    auto_mistake_class_from_alpha,
    compute_portfolio_heat,
    format_portfolio_heat,
    compute_sector_exposure,
    format_sector_exposure,
    compute_equity_stats,
    format_equity_stats,
    risk_halt_status,
    maintain_drawdown_state,
    edge_ok,
    kill_switch_active,
    set_kill_switch,
    compute_confluence,
    format_confluence,
    compute_base_quality,
    compute_correlations,
    dd_scaling_factor,
    exit_suppressed_tickers,
)
from core.data.market_data import (
    get_market_data,
    invalidate_market_cache,
    get_earnings_warnings,
    get_dividend_warnings,
    fetch_news,
    market_regime,
    get_returns,
    get_period_return,
)
from core.events import (
    check_news_events,
    detect_events,
    should_analyze_events,
    check_stop_loss_take_profit,
    check_price_alerts,
)
from core.llm.analyzer import analyze_portfolio
from core.llm.telemetry.api_usage import (
    can_make_api_call,
    get_daily_usage,
    get_minutes_since_last_analysis,
)
from core.auto_kill import maybe_auto_kill, check_macro_shock
from core.gate_log import read_gate_blocks
from core.types import (
    CashMovement, ClosedTrade, Recommendation, Trade, TriggeredEvent, WatchLevel,
)

__all__ = [
    # portfolio
    "portfolio_lock", "paper_lock", "load_portfolio", "save_portfolio",
    "load_paper_portfolio", "save_paper_portfolio", "build_trade_dict",
    "add_cash_movement",
    "suggest_position_size", "compute_kelly_mult", "compute_slippage_budget",
    "compute_hit_stats", "format_hit_stats", "auto_mistake_class_from_alpha",
    "compute_portfolio_heat", "format_portfolio_heat",
    "compute_sector_exposure", "format_sector_exposure",
    "compute_equity_stats", "format_equity_stats",
    "risk_halt_status", "maintain_drawdown_state", "edge_ok",
    "kill_switch_active", "set_kill_switch",
    "compute_confluence", "format_confluence",
    "compute_base_quality",
    "compute_correlations", "dd_scaling_factor",
    "exit_suppressed_tickers",
    # market data
    "get_market_data", "invalidate_market_cache",
    "get_earnings_warnings", "get_dividend_warnings", "fetch_news", "market_regime",
    "get_returns", "get_period_return",
    # events
    "check_news_events", "detect_events", "should_analyze_events",
    "check_stop_loss_take_profit", "check_price_alerts",
    # analyzer
    "analyze_portfolio",
    # api usage
    "can_make_api_call", "get_daily_usage", "get_minutes_since_last_analysis",
    # auto kill + gate log
    "maybe_auto_kill", "check_macro_shock", "read_gate_blocks",
    # typed schemas
    "Trade", "ClosedTrade", "Recommendation", "WatchLevel",
    "CashMovement", "TriggeredEvent",
]
