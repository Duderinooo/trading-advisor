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
};

export type WatchLevel = {
  ticker: string;
  type: string;
  trigger_price: number;
  note?: string;
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
  dd_halt_active?: boolean;
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
