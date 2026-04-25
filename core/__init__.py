"""Public API for the core trading logic.

Re-exports the common surface so callers can write:
    from core import analyze_portfolio, check_price_alerts, ...

Internals are still accessible via their submodules (core.portfolio, ...).
"""

from core.portfolio import (
    portfolio_lock,
    load_portfolio,
    save_portfolio,
    suggest_position_size,
    compute_hit_stats,
    format_hit_stats,
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
)
from core.market_data import (
    get_market_data,
    invalidate_market_cache,
    get_earnings_warnings,
    fetch_news,
    market_regime,
)
from core.events import (
    check_news_events,
    detect_events,
    should_analyze_events,
    check_stop_loss_take_profit,
    check_price_alerts,
)
from core.analyzer import analyze_portfolio
from core.api_usage import (
    can_make_api_call,
    get_daily_usage,
    get_minutes_since_last_analysis,
)

__all__ = [
    # portfolio
    "portfolio_lock", "load_portfolio", "save_portfolio",
    "suggest_position_size",
    "compute_hit_stats", "format_hit_stats",
    "compute_portfolio_heat", "format_portfolio_heat",
    "compute_sector_exposure", "format_sector_exposure",
    "compute_equity_stats", "format_equity_stats",
    "risk_halt_status", "maintain_drawdown_state", "edge_ok",
    "kill_switch_active", "set_kill_switch",
    # market data
    "get_market_data", "invalidate_market_cache",
    "get_earnings_warnings", "fetch_news", "market_regime",
    # events
    "check_news_events", "detect_events", "should_analyze_events",
    "check_stop_loss_take_profit", "check_price_alerts",
    # analyzer
    "analyze_portfolio",
    # api usage
    "can_make_api_call", "get_daily_usage", "get_minutes_since_last_analysis",
]
