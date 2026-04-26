export type RedTeamReview = {
  verdict: "APPROVE" | "WEAKEN" | "KILL";
  confidence_thesis_holds: number;
  top_failure_modes: string[];
  reason: string;
};

export type OpenTrade = {
  ticker: string;
  entry_price: number;
  shares: number;
  size_eur?: number;
  stop_loss: number;
  take_profit: number | number[] | null;
  trailing_stop_pct?: number | null;
  conviction?: number;
  thesis?: string;
  entry_date: string;
  status: "open";
  partial_seq?: number;
  setup_type?: string;
  p_win?: number;
  hold_days_max?: number;
  hold_days_min?: number;
  red_team_review?: RedTeamReview;
};

export type ClosedTrade = {
  ticker: string;
  name?: string;
  entry_price: number;
  shares: number;
  invested?: number;
  stop_loss?: number;
  take_profit?: number | number[];
  entry_date: string;
  entry_reason?: string;
  status: "closed";
  exit_price: number;
  exit_reason?: string;
  exit_date: string;
  pnl_eur: number;
  pnl_pct: number;
  conviction?: number;
  p_win?: number;
  brier?: number;
  outcome?: number;
  partial?: boolean;
  partial_seq?: number;
  mistake_tag?: string;
  mistake_class?: string;
  setup_type?: string;
};

export type WatchLevel = {
  ticker: string;
  type: string;
  trigger_price: number;
  note?: string;
};

export type Heartbeat = {
  last_tick: string;
  market_hours: boolean;
  api_calls_today: number;
  api_cap: number;
};

export type CorrelationMatrix = {
  tickers: string[];
  matrix: Record<string, Record<string, number>>;
  lookback_days: number;
  computed_at: string;
};

export type Portfolio = {
  open_trades: OpenTrade[];
  closed_trades: ClosedTrade[];
  watch_levels: WatchLevel[];
  cash_eur: number;
  total_capital_eur: number;
  last_analysis?: string;
  last_updated?: string;
  notes?: string;
  kill_switch_active?: boolean;
  kill_switch?: boolean;
  kill_switch_reason?: string;
  kill_switch_ts?: string;
  dd_halt_active?: boolean;
  heartbeat?: Heartbeat;
  correlation_matrix?: CorrelationMatrix;
};

export type GateBlock = {
  ts: string;
  ticker: string;
  gate: string;
  blocked: boolean;
  reason: string;
  context: Record<string, unknown>;
};

export type GateAttribution = {
  gate: string;
  blocks: number;
  passes: number;
  block_rate: number;
};

export type CalibrationBin = {
  range: string;
  predicted: number;
  actual: number;
  n: number;
};

export type ThesisDecayFlag = {
  ticker: string;
  held_days: number;
  hold_max: number;
  pnl_pct: number;
  severity: "info" | "warn" | "stale";
};

export type MistakeTrendPoint = {
  bucket: string;
  prediction: number;
  timing: number;
  execution: number;
  external: number;
  untagged: number;
  total: number;
};

export type ShockResult = {
  ticker: string;
  shocked_price: number;
  hits_sl: boolean;
  loss_eur: number;
  loss_pct: number;
};

export type EquityPoint = {
  date: string;
  equity: number;
  peak: number;
  dd_pct: number;
};

export type HitStats = {
  total: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  r_multiple: number | null;
  total_pnl_eur: number;
  streak: string;
  conv_breakdown: Record<number, { wins: number; total: number; rate: number }>;
  calibration: {
    n: number;
    avg_brier: number;
    avg_p_predicted: number;
    actual_win_rate: number;
    bias: number;
    haircut: number;
  } | null;
  mistake_classes: Record<string, number>;
};
