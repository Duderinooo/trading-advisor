"""core.portfolio — portfolio state + risk math + scoring.

Split from monolithic core/portfolio.py (1471 LOC) into focused submodules.
Public API preserved: every name previously importable from core.portfolio is
re-exported here so `from core.portfolio import X` keeps working.
"""

from core.portfolio.cash_movements import (
    effective_pnl_eur, effective_pnl_pct, trade_dividends,
)
from core.portfolio.cooldowns import (
    active_entry_gate_cooldowns, exit_suppressed_tickers,
    record_entry_gate_cooldown,
)
from core.portfolio.correlation import compute_correlations
from core.portfolio.heat import (
    compute_equity_stats, compute_portfolio_heat, compute_sector_exposure,
    format_equity_stats, format_portfolio_heat, format_sector_exposure,
)
from core.portfolio.hit_stats import (
    auto_mistake_class_from_alpha, compute_hit_stats, format_hit_stats,
)
from core.portfolio.io import (
    _PAPER_PORTFOLIO_PATH, _PORTFOLIO_PATH,
    load_paper_portfolio, load_portfolio,
    paper_lock, portfolio_lock,
    save_paper_portfolio, save_portfolio,
)
from core.portfolio.risk import (
    _equity_curve, _today_realized_pnl_eur,
    dd_scaling_factor, edge_ok,
    kill_switch_active, maintain_drawdown_state,
    risk_halt_status, set_kill_switch,
)
from core.portfolio.scoring import (
    compute_base_quality, compute_confluence, format_confluence,
)
from core.portfolio.sizing import (
    compute_kelly_mult, compute_slippage_budget,
    max_affordable_share_price_eur, suggest_position_size,
)
from core.portfolio.trades import (
    PROMPT_VERSION, SNAPSHOT_SCHEMA_VERSION, STRATEGY_VERSION,
    add_cash_movement, build_trade_dict,
)

__all__ = [
    # io + locks
    "portfolio_lock", "paper_lock",
    "load_portfolio", "save_portfolio",
    "load_paper_portfolio", "save_paper_portfolio",
    # trades + cash
    "build_trade_dict", "add_cash_movement",
    "trade_dividends", "effective_pnl_eur", "effective_pnl_pct",
    "STRATEGY_VERSION", "PROMPT_VERSION", "SNAPSHOT_SCHEMA_VERSION",
    # cooldowns
    "exit_suppressed_tickers", "record_entry_gate_cooldown",
    "active_entry_gate_cooldowns",
    # sizing
    "suggest_position_size", "compute_kelly_mult", "compute_slippage_budget",
    "max_affordable_share_price_eur",
    # risk
    "risk_halt_status", "maintain_drawdown_state", "dd_scaling_factor",
    "edge_ok", "kill_switch_active", "set_kill_switch",
    # scoring
    "compute_confluence", "compute_base_quality", "format_confluence",
    # correlation
    "compute_correlations",
    # hit stats
    "compute_hit_stats", "format_hit_stats",
    # heat / sector / equity
    "compute_portfolio_heat", "format_portfolio_heat",
    "compute_sector_exposure", "format_sector_exposure",
    "compute_equity_stats", "format_equity_stats",
]
