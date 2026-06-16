"""Typed schemas for portfolio.json dict-shapes.

Why TypedDict (not @dataclass): portfolio.json persists raw dicts on disk;
migrating to dataclass would force a serialization layer + schema-versioning
migration. TypedDict gives mypy + IDE-autocomplete benefits at zero runtime
cost — dicts stay dicts.

`total=False` everywhere because field-presence is intentionally loose:
- legacy entries pre-date some fields (e.g. base_quality_at_entry added 2026-05-20)
- closed-trade fields (exit_price, mistake_tag) absent on open trades
- partial-close fields absent until first TP1 hit

Callers may type-annotate at boundaries (`def handle_X(rec: Recommendation) ...`)
to surface typos + autocomplete; at runtime nothing changes.
"""

from typing import TypedDict


class WatchLevel(TypedDict, total=False):
    """One entry in portfolio.watch_levels.

    Sonnet emits via set_watch_levels tool. Detected/triggered by
    core/events/watchlevels.detect_events.
    """
    ticker: str
    type: str                       # breakout_long | support_bounce | resistance_reject | ...
    trigger_price: float
    thesis: str
    valid_until: str                # ISO date
    created_date: str               # ISO date
    source: str                     # sonnet_morning | geo_news_auto | manual_/watch
    # Optional confirm gates
    confirm_close_above: float
    invalidate_below: float
    min_volume_ratio: float
    # Zone-mode (alternative to line trigger)
    zone_low: float
    zone_high: float
    # Set by events when ticker first armed (debug/telemetry only)
    note: str


class Recommendation(TypedDict, total=False):
    """One entry in portfolio.pending_recommendations.

    Four kinds (via "kind" discriminator): entry / add / update / exit.
    Persisted by core/llm/handlers/recs/*.py handlers after gate-pass.
    """
    kind: str                       # entry | add | update | exit (missing = "entry")
    ticker: str
    timestamp: str                  # "YYYY-MM-DD HH:MM"
    status: str                     # pending | accepted | dropped
    message_id: int                 # Telegram message_id for /confirm reply lookup

    # ---- entry kind ----
    entry_price: float
    stop_loss: float
    take_profit: list[float] | float
    size_eur: float
    conviction: int                 # 1-5
    p_win: float                    # 0..1
    thesis: str
    setup_type: str
    top_fail_mode: str
    hold_days_min: int
    hold_days_max: int
    direction: str                  # LONG (default)
    trailing_stop_pct: float
    # Decision-context (v7 schema)
    entry_state: str
    primary_signal: str
    why_now: str
    # Regime + provenance
    regime_at_entry: str
    vix_at_entry: float
    spy_above_ma200: bool
    model: str
    prompt_version: str
    strategy_version: str
    decision_version: str
    # Gate-applied modifiers (stamped at gate-pass)
    confluence_score: int
    confluence_items: dict
    correlations: dict
    watch_thesis: str
    auto_split_tp: bool
    kelly_clamp: dict
    vix_dampener: dict
    dd_soft_scale: dict
    red_team_review: dict

    # ---- add kind ----
    additional_size_eur: float
    trigger: str
    thesis_reinforcement: str

    # ---- update kind ----
    new_stop_loss: float
    new_take_profit: list[float] | float
    reason: str

    # ---- exit kind ----
    urgency: str                    # now | today | eod
    reminder_sent: bool
    last_reminder_at: str


class Trade(TypedDict, total=False):
    """One entry in portfolio.open_trades.

    Created by core.portfolio.build_trade_dict in the /confirm flow. Mutated
    over the lifetime of the position (trailing-stop ratchet, partial-close,
    BE-shift, MAE/MFE).
    """
    ticker: str
    entry_price: float
    shares: float                   # TR-Bruchstücke supported
    size_eur: float
    stop_loss: float
    take_profit: list[float] | float | None
    conviction: int
    p_win: float
    thesis: str
    setup_type: str
    top_fail_mode: str
    hold_days_min: int
    hold_days_max: int
    trailing_stop_pct: float | None
    # Decision-context (v7)
    entry_state: str
    primary_signal: str
    why_now: str
    # Regime + provenance
    regime_at_entry: str
    vix_at_entry: float
    spy_above_ma200: bool
    model: str
    prompt_version: str
    strategy_version: str
    decision_version: str
    snapshot_schema_version: str
    # Quality
    base_quality_at_entry: int      # 0-10
    confluence_at_entry: int        # 0-10
    # Execution-context
    market_session_at_entry: str    # xetra | us_overlap | after_hours
    spread_pct_at_entry: float
    gap_at_open_pct_at_entry: float
    # R-Multiple tracking
    initial_risk_per_share: float
    realized_r: float | None        # set at close
    max_r_open: float
    # Partial-TP tracking
    partial_close_count: int
    total_partial_pnl_eur: float
    partial_seq: int
    # Lifecycle
    entry_date: str                 # "YYYY-MM-DD HH:MM"
    status: str                     # open | partial_exit | closed | closed_partial
    entry_snapshot: dict | None
    # MAE/MFE (mutated by heartbeat tick)
    mae: float
    mfe: float
    # Fees
    entry_fee_eur: float
    # Sonnet morning watch context (kept across confirm for thesis fidelity)
    watch_thesis: str
    # Pending-exit cooldown state
    exit_dropped_at: str
    alerted_keys: list[str]
    # Pyramiding log
    add_history: list[dict]
    update_history: list[dict]
    # Gate-modifier audit
    auto_split_tp: bool
    kelly_clamp: dict
    vix_dampener: dict
    dd_soft_scale: dict
    correlations: dict
    confluence_score: int
    confluence_items: dict
    red_team_review: dict
    # Slippage at fill
    rec_entry_price: float
    slippage_pct: float


class ClosedTrade(Trade, total=False):
    """A Trade after exit. Adds exit_* fields + Brier scoring."""
    exit_price: float
    exit_date: str                  # "YYYY-MM-DD HH:MM"
    exit_reason: str                # STOP_LOSS | TAKE_PROFIT | TAKE_PROFIT_PARTIAL | MANUAL_CLOSE | THESIS_BREAK
    exit_type: str                  # sl_hit | tp_hit | tp_partial | manual | thesis_break | other
    pnl_eur: float
    pnl_pct: float
    exit_fee_eur: float
    holding_days_realized: float
    # Brier scoring (only on first-close — TP1 partial or full close)
    brier: float
    outcome: int                    # 1 = win, 0 = loss
    # Alpha/Beta attribution
    spy_return_pct: float
    alpha_pct: float
    # Mistake taxonomy (manual or auto-tagged)
    mistake_tag: str | None
    mistake_class: str | None
    sl_exit_slippage_pct: float
    # Partial flag — closed_partial entries set this
    partial: bool


class CashMovement(TypedDict, total=False):
    """One entry in portfolio.cash_movements.

    Records dividends, manual deposits/withdrawals, fee adjustments. Linked
    to the trade that was open on `ex_date` for dividend-attribution.
    """
    date: str                       # "YYYY-MM-DD"
    amount: float                   # signed (positive = inflow)
    kind: str                       # dividend | deposit | withdrawal | fee_adjust
    ticker: str
    note: str
    ex_date: str
    linked_trade: dict              # {ticker, entry_date, exit_date, status}


class GeoNewsFiredEntry(TypedDict):
    """Value in portfolio.geo_news_fired dict — per-commodity-set timestamp."""
    pass  # values are ISO timestamps; keys = "comm1+comm2" sorted joined


class TriggeredEvent(TypedDict, total=False):
    """One entry in portfolio.triggered_events (TTL-deduped watch hits)."""
    key: str                        # _get_event_key output
    date: str
    ts: str
    time: str
