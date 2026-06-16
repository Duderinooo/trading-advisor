import type {
  CalibrationBin,
  CashMovement,
  ClosedTrade,
  EquityPoint,
  GateAttribution,
  GateBlock,
  HitStats,
  MistakeTrendPoint,
  OpenTrade,
  Portfolio,
  SetupTypeStats,
  ShockResult,
  ThesisDecayFlag,
} from "./types";

// Min scored trades before the Brier-haircut activates. Mirrors
// config.MIN_CALIBRATION_N in the Python bot — below this, bias is noise.
export const MIN_CALIBRATION_N = 10;

export function computeEquityCurve(p: Portfolio): EquityPoint[] {
  // Two data sources merged:
  //   1. realized events (closed_trade exits + cash_movements) — the historical
  //      anchors. Each event jumps the curve at its date.
  //   2. equity_history snapshots — minute-by-minute heartbeat samples. These
  //      densify the curve into a real intraday line instead of a step plot.
  // Both feed a unified sorted event stream; we walk it forward maintaining
  // running peak + drawdown.
  type Event = { date: string; equity?: number; delta?: number };
  const events: Event[] = [];
  for (const t of p.closed_trades) {
    if (!t.exit_date) continue;
    events.push({ date: t.exit_date, delta: Number(t.pnl_eur ?? 0) });
  }
  for (const m of p.cash_movements ?? []) {
    if (!m.date) continue;
    events.push({ date: m.date, delta: Number(m.amount ?? 0) });
  }
  for (const s of p.equity_history ?? []) {
    if (!s.ts) continue;
    events.push({ date: s.ts, equity: Number(s.equity ?? 0) });
  }
  events.sort((a, b) => a.date.localeCompare(b.date));

  let equity = p.total_capital_eur;
  let peak = equity;
  const points: EquityPoint[] = [
    { ts: "start", date: "start", equity, peak, dd_pct: 0 },
  ];
  for (const e of events) {
    if (typeof e.equity === "number") {
      // Snapshot — set absolute equity (already includes realized + unrealized)
      equity = e.equity;
    } else if (typeof e.delta === "number") {
      // Delta event (closed_trade or movement) — additive
      equity += e.delta;
    }
    if (equity > peak) peak = equity;
    const dd_pct = peak > 0 ? ((peak - equity) / peak) * 100 : 0;
    // ts preserves intraday HH:MM; date stays YYYY-MM-DD for sparkline filters.
    const ts = e.date.includes(" ") ? e.date.replace(" ", "T") : e.date;
    points.push({
      ts,
      date: e.date.split(" ")[0],
      equity: Math.round(equity * 100) / 100,
      peak: Math.round(peak * 100) / 100,
      dd_pct: Math.round(dd_pct * 100) / 100,
    });
  }

  // Live "now" point: only append if there's no recent equity_history snapshot
  // (within last 5 min). Avoids double-rendering when bot heartbeat is fresh.
  const liveQuotes = p.heartbeat?.live_quotes ?? {};
  const fallbackPrices = p.heartbeat?.prices ?? {};
  let unrealized = 0;
  let liveCount = 0;
  for (const t of p.open_trades) {
    const lq = liveQuotes[t.ticker]?.price;
    const fb = fallbackPrices[t.ticker];
    const live = typeof lq === "number" ? lq : typeof fb === "number" ? fb : null;
    const entry = Number(t.entry_price ?? 0);
    const shares = Number(t.shares ?? 0);
    if (live != null && entry > 0 && shares > 0) {
      unrealized += (live - entry) * shares;
      liveCount += 1;
    }
  }
  const recentHistory = (p.equity_history ?? []).slice(-1)[0];
  const recentTs = recentHistory?.ts;
  let recentFresh = false;
  if (recentTs) {
    const recentMs = new Date(recentTs.replace(" ", "T")).getTime();
    if (Number.isFinite(recentMs)) {
      recentFresh = Date.now() - recentMs < 5 * 60 * 1000;
    }
  }
  if (liveCount > 0 && !recentFresh) {
    const liveEquity = Math.round(equity + unrealized);
    const livePeak = Math.max(peak, liveEquity);
    const dd_pct = livePeak > 0 ? ((livePeak - liveEquity) / livePeak) * 100 : 0;
    points.push({
      ts: "now",
      date: "now",
      equity: liveEquity,
      peak: livePeak,
      dd_pct: Math.round(dd_pct * 10) / 10,
    });
  }
  return points;
}

// Rounded live equity used to key curve memoization in Dashboard. Whole € so
// cent-level price ticks don't bust the cache.
export function liveEquityRounded(p: Portfolio): number {
  const liveQuotes = p.heartbeat?.live_quotes ?? {};
  const fallbackPrices = p.heartbeat?.prices ?? {};
  let unrealized = 0;
  for (const t of p.open_trades) {
    const lq = liveQuotes[t.ticker]?.price;
    const fb = fallbackPrices[t.ticker];
    const live = typeof lq === "number" ? lq : typeof fb === "number" ? fb : null;
    const entry = Number(t.entry_price ?? 0);
    const shares = Number(t.shares ?? 0);
    if (live != null && entry > 0 && shares > 0) {
      unrealized += (live - entry) * shares;
    }
  }
  return Math.round(currentEquity(p) + unrealized);
}

// Sum dividends linked to a specific closed trade (composite-key match: ticker
// + entry_date + exit_date + status). Mirrors core.portfolio.trade_dividends.
function tradeDividends(t: ClosedTrade, movements: CashMovement[]): number {
  if (!movements.length) return 0;
  let sum = 0;
  for (const m of movements) {
    if (m.kind !== "dividend") continue;
    const link = m.linked_trade;
    if (!link) continue;
    if (link.ticker.toUpperCase() !== t.ticker.toUpperCase()) continue;
    if (link.entry_date !== t.entry_date) continue;
    if (link.exit_date !== t.exit_date) continue;
    sum += Number(m.amount ?? 0);
  }
  return sum;
}

function effectivePnlPct(t: ClosedTrade, movements: CashMovement[]): number {
  const base = Number(t.pnl_pct ?? 0);
  const divs = tradeDividends(t, movements);
  if (!divs) return base;
  let sizeEur = 0;
  // Reconstruct trade size from entry × shares (size_eur not on ClosedTrade type).
  const entry = Number(t.entry_price ?? 0);
  const shares = Number(t.shares ?? 0);
  sizeEur = entry * shares;
  if (sizeEur <= 0) return base;
  return base + (divs / sizeEur) * 100;
}

function effectivePnlEur(t: ClosedTrade, movements: CashMovement[]): number {
  return Number(t.pnl_eur ?? 0) + tradeDividends(t, movements);
}

export function computeHitStats(
  closed: ClosedTrade[],
  movements: CashMovement[] = [],
): HitStats | null {
  if (closed.length < 3) return null;
  // No `partial` filter: each closed_trades entry IS a realized event (partial-TP
  // = locked-in profit on portion). Filtering dropped SIE.DE win on 2026-05-07
  // when its partial-TP entry was the only record. Backend (compute_hit_stats)
  // doesn't filter — keep parity.
  // pnl_pct < 0 (not <= 0): exact-zero is break-even, neither win nor loss.
  // Effective pnl: includes dividends linked to the trade.
  const effPct = (t: ClosedTrade) => effectivePnlPct(t, movements);
  const effEur = (t: ClosedTrade) => effectivePnlEur(t, movements);
  const wins = closed.filter((t) => effPct(t) > 0);
  const losses = closed.filter((t) => effPct(t) < 0);
  const total = closed.length;
  const avgWin =
    wins.length > 0
      ? wins.reduce((s, t) => s + effPct(t), 0) / wins.length
      : 0;
  const avgLoss =
    losses.length > 0
      ? losses.reduce((s, t) => s + effPct(t), 0) / losses.length
      : 0;
  const rMultiple = avgLoss !== 0 ? avgWin / Math.abs(avgLoss) : null;

  const byConv: Record<number, ClosedTrade[]> = {};
  for (const t of closed) {
    if (typeof t.conviction === "number") {
      const c = Math.floor(t.conviction);
      (byConv[c] ??= []).push(t);
    }
  }
  const convBreakdown: HitStats["conv_breakdown"] = {};
  for (const [c, ts] of Object.entries(byConv)) {
    const w = ts.filter((t) => effPct(t) > 0).length;
    convBreakdown[Number(c)] = {
      wins: w,
      total: ts.length,
      rate: ts.length ? Math.round((w / ts.length) * 1000) / 10 : 0,
    };
  }

  const streak = closed
    .slice(-5)
    .map((t) => (effPct(t) > 0 ? "W" : "L"))
    .join("");

  const totalPnlEur =
    Math.round(closed.reduce((s, t) => s + effEur(t), 0) * 100) / 100;

  const scored = closed
    .slice(-20)
    .filter(
      (t) =>
        typeof t.p_win === "number" && typeof t.brier === "number",
    );
  let calibration: HitStats["calibration"] = null;
  if (scored.length > 0) {
    const n = scored.length;
    const avgBrier = scored.reduce((s, t) => s + (t.brier ?? 0), 0) / n;
    const avgPPred = scored.reduce((s, t) => s + (t.p_win ?? 0), 0) / n;
    const actual = scored.reduce((s, t) => s + (t.outcome ?? 0), 0) / n;
    const bias = avgPPred - actual;
    // Haircut only fires at MIN_CALIBRATION_N+ scored trades — below that, bias
    // is noise. Mirrors the Python guard in core.portfolio.compute_hit_stats.
    const haircut =
      n >= MIN_CALIBRATION_N && Math.abs(bias) >= 0.05
        ? Math.round(bias * 1000) / 1000
        : 0;
    calibration = {
      n,
      avg_brier: Math.round(avgBrier * 10000) / 10000,
      avg_p_predicted: Math.round(avgPPred * 1000) / 1000,
      actual_win_rate: Math.round(actual * 1000) / 1000,
      bias: Math.round(bias * 1000) / 1000,
      haircut,
    };
  }

  const recentLosses = closed
    .filter((t) => effPct(t) < 0)
    .slice(-20);
  const mistakeClasses: Record<string, number> = {};
  for (const t of recentLosses) {
    const cls = t.mistake_class ?? "untagged";
    mistakeClasses[cls] = (mistakeClasses[cls] ?? 0) + 1;
  }

  return {
    total,
    wins: wins.length,
    losses: losses.length,
    win_rate:
      total > 0 ? Math.round((wins.length / total) * 1000) / 10 : 0,
    avg_win_pct: Math.round(avgWin * 100) / 100,
    avg_loss_pct: Math.round(avgLoss * 100) / 100,
    r_multiple:
      rMultiple !== null ? Math.round(rMultiple * 100) / 100 : null,
    total_pnl_eur: totalPnlEur,
    streak,
    conv_breakdown: convBreakdown,
    calibration,
    mistake_classes: mistakeClasses,
  };
}

export function computeSetupTypeStats(
  closed: ClosedTrade[],
  movements: CashMovement[] = [],
): SetupTypeStats[] {
  const effPct = (t: ClosedTrade) => effectivePnlPct(t, movements);
  const effEur = (t: ClosedTrade) => effectivePnlEur(t, movements);
  const buckets = new Map<string, ClosedTrade[]>();
  for (const t of closed) {
    // No partial filter — backend parity (each closed entry is a realized event).
    const key = t.setup_type ?? "untagged";
    const arr = buckets.get(key) ?? [];
    arr.push(t);
    buckets.set(key, arr);
  }
  const out: SetupTypeStats[] = [];
  for (const [setup_type, trades] of buckets) {
    const total = trades.length;
    const wins = trades.filter((t) => effPct(t) > 0).length;
    const sumPnlPct = trades.reduce((s, t) => s + effPct(t), 0);
    const sumPnlEur = trades.reduce((s, t) => s + effEur(t), 0);
    out.push({
      setup_type,
      total,
      wins,
      win_rate: total > 0 ? Math.round((wins / total) * 1000) / 10 : 0,
      avg_pnl_pct: total > 0 ? Math.round((sumPnlPct / total) * 100) / 100 : 0,
      total_pnl_eur: Math.round(sumPnlEur * 100) / 100,
    });
  }
  out.sort((a, b) => b.total - a.total);
  return out;
}

// Pre-mortem accuracy: did Sonnet's predicted top_fail_mode (entry-time)
// match the actual mistake_class (close-time tag)? Mirrors Python mapping in
// core/portfolio.compute_hit_stats fail_mode_to_class.
const FAIL_MODE_TO_CLASS: Record<string, string> = {
  support_breakdown: "prediction",
  thesis_invalidation: "prediction",
  earnings_miss: "external",
  macro_event: "external",
  regime_shift: "external",
  sector_rotation: "external",
  false_breakout: "timing",
  stop_run: "timing",
};

export type PreMortemStats = {
  n: number;
  correct: number;
  accuracy_pct: number;
  by_mode: Array<{ mode: string; n: number; correct: number; rate: number }>;
};

export function preMortemAccuracy(closed: ClosedTrade[]): PreMortemStats | null {
  const losses = closed.filter(
    (t) =>
      (t.pnl_pct ?? 0) <= 0 &&
      t.top_fail_mode &&
      t.mistake_class &&
      !t.partial,
  );
  if (losses.length < 3) return null;
  let correct = 0;
  const groups: Record<string, { n: number; correct: number }> = {};
  for (const t of losses) {
    const mode = t.top_fail_mode as string;
    const predicted = FAIL_MODE_TO_CLASS[mode];
    const matched = predicted === t.mistake_class;
    if (matched) correct += 1;
    const g = (groups[mode] ??= { n: 0, correct: 0 });
    g.n += 1;
    if (matched) g.correct += 1;
  }
  const by_mode = Object.entries(groups)
    .map(([mode, g]) => ({
      mode,
      n: g.n,
      correct: g.correct,
      rate: Math.round((g.correct / g.n) * 1000) / 10,
    }))
    .sort((a, b) => b.n - a.n);
  return {
    n: losses.length,
    correct,
    accuracy_pct: Math.round((correct / losses.length) * 1000) / 10,
    by_mode,
  };
}

export type HoldTimeStats = {
  n: number;
  avg_actual: number;
  avg_planned: number;
  early_exit_rate: number;   // share closed before hold_days_min
  overstay_rate: number;     // share closed after hold_days_max
};

function _daysBetween(a: string, b: string): number {
  const ta = Date.parse(a.split(" ")[0]);
  const tb = Date.parse(b.split(" ")[0]);
  if (Number.isNaN(ta) || Number.isNaN(tb)) return NaN;
  return Math.max(0, (tb - ta) / 86_400_000);
}

export function holdTimeStats(closed: ClosedTrade[]): HoldTimeStats | null {
  const valid = closed.filter(
    (t) =>
      !t.partial &&
      t.entry_date &&
      t.exit_date &&
      typeof t.hold_days_max === "number",
  );
  if (valid.length < 3) return null;
  let sumActual = 0;
  let sumPlanned = 0;
  let early = 0;
  let over = 0;
  for (const t of valid) {
    const d = _daysBetween(t.entry_date, t.exit_date as string);
    if (Number.isNaN(d)) continue;
    sumActual += d;
    const planned = Number(t.hold_days_max ?? 0);
    sumPlanned += planned;
    const minHold = Number(t.hold_days_min ?? 0);
    if (minHold > 0 && d < minHold) early += 1;
    if (planned > 0 && d > planned) over += 1;
  }
  return {
    n: valid.length,
    avg_actual: Math.round((sumActual / valid.length) * 10) / 10,
    avg_planned: Math.round((sumPlanned / valid.length) * 10) / 10,
    early_exit_rate: Math.round((early / valid.length) * 1000) / 10,
    overstay_rate: Math.round((over / valid.length) * 1000) / 10,
  };
}

// Risk-of-Ruin Monte Carlo: bootstrap-resample closed-trade pnl_pct sequence,
// run N trials of M-trade horizons, count how often equity falls below
// (1 - ruinPct/100) of starting capital.
export type RuinStats = {
  n_trials: number;
  horizon: number;
  ruin_threshold_pct: number;
  ruin_prob_pct: number;
  median_terminal_return_pct: number;
  worst_5pct_return_pct: number;
};

export function monteCarloRuin(
  closed: ClosedTrade[],
  opts: { trials?: number; horizon?: number; ruinPct?: number } = {},
): RuinStats | null {
  const trials = opts.trials ?? 5000;
  const horizon = opts.horizon ?? 100;
  const ruinPct = opts.ruinPct ?? 25;
  const final = closed.filter((t) => !t.partial && typeof t.pnl_pct === "number");
  if (final.length < 10) return null;
  const returns = final.map((t) => Number(t.pnl_pct) / 100);
  const ruinFactor = 1 - ruinPct / 100;
  const terminals: number[] = [];
  let busts = 0;
  for (let i = 0; i < trials; i++) {
    let eq = 1;
    let bust = false;
    for (let j = 0; j < horizon; j++) {
      // Position-size assumption: each trade is 5% of equity (typical risk per
      // trade in this bot). Scale pnl_pct by 0.05 so distribution matches a
      // realistic single-trade equity impact.
      const r = returns[Math.floor(Math.random() * returns.length)] * 0.05;
      eq *= 1 + r;
      if (eq <= ruinFactor) {
        bust = true;
        break;
      }
    }
    if (bust) busts += 1;
    terminals.push(eq);
  }
  terminals.sort((a, b) => a - b);
  const median = terminals[Math.floor(trials / 2)];
  const worst5 = terminals[Math.floor(trials * 0.05)];
  return {
    n_trials: trials,
    horizon,
    ruin_threshold_pct: ruinPct,
    ruin_prob_pct: Math.round((busts / trials) * 1000) / 10,
    median_terminal_return_pct: Math.round((median - 1) * 1000) / 10,
    worst_5pct_return_pct: Math.round((worst5 - 1) * 1000) / 10,
  };
}

export function maxLossStreak(closed: ClosedTrade[]): { current: number; max: number } {
  const final = closed
    .filter((t) => !t.partial && typeof t.pnl_pct === "number")
    .slice()
    .sort((a, b) => (a.exit_date ?? "").localeCompare(b.exit_date ?? ""));
  let cur = 0;
  let max = 0;
  for (const t of final) {
    if ((t.pnl_pct ?? 0) <= 0) {
      cur += 1;
      if (cur > max) max = cur;
    } else {
      cur = 0;
    }
  }
  return { current: cur, max };
}

// MAE/MFE per closed trade, normalized in R-multiples (R = entry → SL distance).
// Insight: if winners' avg MAE ≈ -0.3R but losers stop at -1.0R, SL +0.3R wider
// would catch them all without changing winners. Symmetric for MFE → tightening.
export type MaeMfeRow = {
  ticker: string;
  exit_date: string;
  pnl_pct: number;
  mae_r: number | null;     // worst excursion in R-multiples (negative)
  mfe_r: number | null;     // best excursion in R-multiples (positive)
  outcome: "win" | "loss";
};

export type MaeMfeStats = {
  rows: MaeMfeRow[];
  win_avg_mae_r: number | null;
  loss_avg_mae_r: number | null;
  win_avg_mfe_r: number | null;
  loss_avg_mfe_r: number | null;
};

export function maeMfeAnalysis(closed: ClosedTrade[]): MaeMfeStats | null {
  const rows: MaeMfeRow[] = [];
  for (const t of closed) {
    if (t.partial) continue;
    const entry = Number(t.entry_price ?? 0);
    const sl = Number(t.stop_loss ?? 0);
    if (entry <= 0 || sl <= 0 || entry <= sl) continue;
    const r = entry - sl;
    const mae = typeof t.mae === "number" ? t.mae : null;
    const mfe = typeof t.mfe === "number" ? t.mfe : null;
    if (mae == null && mfe == null) continue;
    rows.push({
      ticker: t.ticker,
      exit_date: t.exit_date ?? "",
      pnl_pct: Number(t.pnl_pct ?? 0),
      mae_r: mae != null ? Math.round(((mae - entry) / r) * 100) / 100 : null,
      mfe_r: mfe != null ? Math.round(((mfe - entry) / r) * 100) / 100 : null,
      outcome: (t.pnl_pct ?? 0) > 0 ? "win" : "loss",
    });
  }
  if (rows.length === 0) return null;
  const wins = rows.filter((r) => r.outcome === "win");
  const losses = rows.filter((r) => r.outcome === "loss");
  const avg = (xs: (number | null)[]): number | null => {
    const valid = xs.filter((x): x is number => typeof x === "number");
    if (valid.length === 0) return null;
    return Math.round((valid.reduce((s, v) => s + v, 0) / valid.length) * 100) / 100;
  };
  return {
    rows: rows.sort((a, b) => b.exit_date.localeCompare(a.exit_date)),
    win_avg_mae_r: avg(wins.map((r) => r.mae_r)),
    loss_avg_mae_r: avg(losses.map((r) => r.mae_r)),
    win_avg_mfe_r: avg(wins.map((r) => r.mfe_r)),
    loss_avg_mfe_r: avg(losses.map((r) => r.mfe_r)),
  };
}

export function currentEquity(p: Portfolio): number {
  let eq = p.total_capital_eur;
  for (const t of p.closed_trades) eq += Number(t.pnl_eur ?? 0);
  for (const m of p.cash_movements ?? []) eq += Number(m.amount ?? 0);
  return Math.round(eq * 100) / 100;
}

export function unrealizedPnl(p: Portfolio): number {
  const liveQuotes = p.heartbeat?.live_quotes ?? {};
  const fallbackPrices = p.heartbeat?.prices ?? {};
  let pnl = 0;
  for (const t of p.open_trades) {
    const lq = liveQuotes[t.ticker]?.price;
    const fb = fallbackPrices[t.ticker];
    const live = typeof lq === "number" ? lq : typeof fb === "number" ? fb : null;
    const entry = Number(t.entry_price ?? 0);
    const shares = Number(t.shares ?? 0);
    if (live != null && entry > 0 && shares > 0) {
      pnl += (live - entry) * shares;
    }
  }
  return Math.round(pnl * 100) / 100;
}

export function currentEquityLive(p: Portfolio): number {
  return Math.round((currentEquity(p) + unrealizedPnl(p)) * 100) / 100;
}

export function todayRealizedLoss(p: Portfolio): { eur: number; pct: number } {
  const today = new Date().toISOString().slice(0, 10);
  const todayClosed = p.closed_trades.filter(
    (t) => (t.exit_date ?? "").startsWith(today),
  );
  const eur = todayClosed.reduce((s, t) => s + Number(t.pnl_eur ?? 0), 0);
  const cap = p.total_capital_eur > 0 ? p.total_capital_eur : 1;
  return { eur: Math.round(eur * 100) / 100, pct: (eur / cap) * 100 };
}

export function portfolioHeat(p: Portfolio): { eur: number; pct: number } {
  let heat = 0;
  for (const t of p.open_trades) {
    const sl = Number(t.stop_loss ?? 0);
    const entry = Number(t.entry_price ?? 0);
    const sh = Number(t.shares ?? 0);
    if (entry > sl && sl > 0 && sh > 0) heat += (entry - sl) * sh;
  }
  const cap = p.total_capital_eur > 0 ? p.total_capital_eur : 1;
  return { eur: Math.round(heat * 100) / 100, pct: (heat / cap) * 100 };
}

export function openExposure(p: Portfolio): number {
  let s = 0;
  for (const t of p.open_trades) {
    s += t.size_eur ?? t.entry_price * t.shares;
  }
  return Math.round(s * 100) / 100;
}

// ---------- Gate attribution ----------

export function aggregateGateBlocks(blocks: GateBlock[]): GateAttribution[] {
  const acc: Record<string, { blocks: number; passes: number }> = {};
  for (const b of blocks) {
    const slot = (acc[b.gate] ??= { blocks: 0, passes: 0 });
    if (b.blocked) slot.blocks += 1;
    else slot.passes += 1;
  }
  const out: GateAttribution[] = Object.entries(acc).map(
    ([gate, s]) => ({
      gate,
      blocks: s.blocks,
      passes: s.passes,
      block_rate:
        s.blocks + s.passes > 0
          ? Math.round((s.blocks / (s.blocks + s.passes)) * 1000) / 10
          : 0,
    }),
  );
  return out.sort((a, b) => b.blocks - a.blocks);
}

// ---------- Gate-activity day digest (bug-hunting meta-review) ----------

export type GateActivityDay = {
  day: string;                              // YYYY-MM-DD
  nBlocks: number;
  nPasses: number;
  byGate: Array<{ gate: string; count: number }>;
  flags: string[];                          // human-readable anomalies
};

// One day's gate decisions aggregated + scanned for anomalies worth a human
// look. The flags are the bug-hunting layer: re-eval spam, Claude incoherence,
// chronic SL-sizing. `day` is a YYYY-MM-DD prefix. Returns null if no events.
export function summarizeGateActivityDay(
  blocks: GateBlock[],
  day: string,
): GateActivityDay | null {
  const events = blocks.filter((e) => (e.ts ?? "").startsWith(day));
  if (events.length === 0) return null;

  const blocked = events.filter((e) => e.blocked);
  const passed = events.filter((e) => !e.blocked);

  const gateCounts = new Map<string, number>();
  const pairCounts = new Map<string, number>();   // `${ticker}|${gate}`
  const slByTicker = new Map<string, number>();
  for (const e of blocked) {
    const gate = e.gate || "?";
    const ticker = e.ticker || "?";
    gateCounts.set(gate, (gateCounts.get(gate) ?? 0) + 1);
    const pk = `${ticker}|${gate}`;
    pairCounts.set(pk, (pairCounts.get(pk) ?? 0) + 1);
    if (gate === "sl_distance") {
      slByTicker.set(ticker, (slByTicker.get(ticker) ?? 0) + 1);
    }
  }

  const flags: string[] = [];

  // Re-eval spam: same ticker + same gate blocked ≥3× — RS/edge are intraday-
  // stable, so repeats mean wasted Claude recs. The entry-gate cooldown should
  // suppress this; a persisting flag means the cooldown is not working.
  for (const [pk, n] of [...pairCounts.entries()].sort((a, b) => b[1] - a[1])) {
    if (n >= 3) {
      const [ticker, gate] = pk.split("|");
      flags.push(`${ticker} × ${gate} ${n}× — Re-Eval-Spam (Cooldown sollte greifen)`);
    }
  }

  // Claude incoherence: recommend_exit on a ticker with no open position.
  const noPos = [...new Set(
    blocked.filter((e) => e.gate === "exit_no_position").map((e) => e.ticker),
  )].sort();
  for (const ticker of noPos) {
    flags.push(`${ticker} exit_no_position — Claude empfahl Exit ohne Position`);
  }

  // Chronic SL-sizing: same ticker hits sl_distance ≥2× — repeated SL-distance
  // misses on one name = a prompt-quality signal, not a one-off.
  for (const [ticker, n] of slByTicker.entries()) {
    if (n >= 2) {
      flags.push(`${ticker} × sl_distance ${n}× — Claude SL-Sizing-Pattern`);
    }
  }

  return {
    day,
    nBlocks: blocked.length,
    nPasses: passed.length,
    byGate: [...gateCounts.entries()]
      .map(([gate, count]) => ({ gate, count }))
      .sort((a, b) => b.count - a.count),
    flags,
  };
}

// Distinct YYYY-MM-DD days present in the gate-log, newest first, capped to
// `limit`. Drives the multi-day gate-activity review.
export function recentGateDays(blocks: GateBlock[], limit = 7): string[] {
  const days = new Set<string>();
  for (const b of blocks) {
    const d = (b.ts ?? "").slice(0, 10);
    if (d.length === 10) days.add(d);
  }
  return [...days].sort((a, b) => b.localeCompare(a)).slice(0, limit);
}

// ---------- Calibration bins (predicted p_win vs realized win-rate) ----------

const BINS: Array<[number, number]> = [
  [0.0, 0.2],
  [0.2, 0.4],
  [0.4, 0.6],
  [0.6, 0.8],
  [0.8, 1.0001],
];

export function computeCalibrationBins(closed: ClosedTrade[]): CalibrationBin[] {
  const final = closed.filter((t) => !t.partial && typeof t.p_win === "number");
  return BINS.map(([lo, hi]) => {
    const inBin = final.filter((t) => (t.p_win ?? 0) >= lo && (t.p_win ?? 0) < hi);
    const n = inBin.length;
    const predicted =
      n > 0 ? inBin.reduce((s, t) => s + (t.p_win ?? 0), 0) / n : 0;
    const actual =
      n > 0
        ? inBin.filter(
            (t) => (t.outcome ?? ((t.pnl_pct ?? 0) > 0 ? 1 : 0)) === 1,
          ).length / n
        : 0;
    return {
      range: `${lo.toFixed(1)}–${hi >= 1 ? "1.0" : hi.toFixed(1)}`,
      predicted: Math.round(predicted * 1000) / 1000,
      actual: Math.round(actual * 1000) / 1000,
      n,
    };
  });
}

// ---------- Thesis-decay flags ----------

export function computeThesisDecay(
  open: OpenTrade[],
  livePrices: Record<string, number> = {},
): ThesisDecayFlag[] {
  const today = new Date();
  const out: ThesisDecayFlag[] = [];
  for (const t of open) {
    const holdMax = t.hold_days_max;
    if (typeof holdMax !== "number" || holdMax <= 0) continue;
    const entry = new Date(t.entry_date.slice(0, 10));
    if (Number.isNaN(entry.getTime())) continue;
    const heldDays = Math.floor(
      (today.getTime() - entry.getTime()) / 86_400_000,
    );
    if (heldDays < Math.floor(holdMax * 0.5)) continue;

    const live = livePrices[t.ticker];
    const refPrice = typeof live === "number" ? live : t.entry_price;
    const pnlPct = t.entry_price > 0
      ? ((refPrice - t.entry_price) / t.entry_price) * 100
      : 0;

    // Stale-thesis tier removed 2026-06-16 (matches backend): holding past the
    // planned horizon is no longer flagged as "stale" — calendar age alone is
    // not thesis decay. Soft "warn" past 75% stays as a heads-up only.
    let severity: ThesisDecayFlag["severity"] = "info";
    if (heldDays >= Math.floor(holdMax * 0.75)) severity = "warn";

    out.push({
      ticker: t.ticker,
      held_days: heldDays,
      hold_max: holdMax,
      pnl_pct: Math.round(pnlPct * 100) / 100,
      severity,
    });
  }
  return out.sort((a, b) => b.held_days - a.held_days);
}

// ---------- What-if shock ----------

export function simulateShock(
  open: OpenTrade[],
  shockPct: number, // negative number = drop, e.g. -3 for SPX -3%
): { results: ShockResult[]; total_loss_eur: number; sl_hits: number } {
  const results: ShockResult[] = [];
  let totalLoss = 0;
  let slHits = 0;
  for (const t of open) {
    const shocked = t.entry_price * (1 + shockPct / 100);
    const hitsSl = t.stop_loss > 0 && shocked <= t.stop_loss;
    const exit = hitsSl ? t.stop_loss : shocked;
    const loss = (exit - t.entry_price) * t.shares;
    totalLoss += loss;
    if (hitsSl) slHits += 1;
    results.push({
      ticker: t.ticker,
      shocked_price: Math.round(shocked * 100) / 100,
      hits_sl: hitsSl,
      loss_eur: Math.round(loss * 100) / 100,
      loss_pct:
        t.entry_price > 0
          ? Math.round(((exit - t.entry_price) / t.entry_price) * 10000) / 100
          : 0,
    });
  }
  return {
    results: results.sort((a, b) => a.loss_eur - b.loss_eur),
    total_loss_eur: Math.round(totalLoss * 100) / 100,
    sl_hits: slHits,
  };
}

// ---------- Mistake-class rolling trend ----------

export function computeMistakeTrend(
  closed: ClosedTrade[],
  windowSize = 10,
): MistakeTrendPoint[] {
  const losses = closed
    .filter((t) => !t.partial && (t.pnl_pct ?? 0) <= 0)
    .filter((t) => t.exit_date)
    .sort((a, b) => a.exit_date.localeCompare(b.exit_date));
  if (losses.length < windowSize) return [];

  const out: MistakeTrendPoint[] = [];
  for (let i = windowSize - 1; i < losses.length; i++) {
    const window = losses.slice(i - windowSize + 1, i + 1);
    const counts = {
      prediction: 0,
      timing: 0,
      execution: 0,
      external: 0,
      untagged: 0,
    };
    for (const l of window) {
      const cls = (l.mistake_class ?? "untagged") as keyof typeof counts;
      if (cls in counts) counts[cls] += 1;
      else counts.untagged += 1;
    }
    out.push({
      bucket: losses[i].exit_date.slice(0, 10),
      ...counts,
      total: window.length,
    });
  }
  return out;
}

// Rolling KPI sparklines (last N points). Each derives from closed-trade sequence.
export type SparkPoint = { v: number };

export function returnSpark(curve: EquityPoint[], n = 14): SparkPoint[] {
  // Use the equity curve's last n points (excluding the synthetic "start" anchor).
  const real = curve.filter((p) => p.ts !== "start");
  return real.slice(-n).map((p) => ({ v: p.equity }));
}

export function winRateSpark(closed: ClosedTrade[], window = 10, points = 14): SparkPoint[] {
  const final = closed
    .filter((t) => !t.partial && typeof t.pnl_pct === "number")
    .sort((a, b) => (a.exit_date ?? "").localeCompare(b.exit_date ?? ""));
  if (final.length < window) return [];
  const out: SparkPoint[] = [];
  for (let i = window - 1; i < final.length; i++) {
    const w = final.slice(i - window + 1, i + 1);
    const wins = w.filter((t) => (t.pnl_pct ?? 0) > 0).length;
    out.push({ v: (wins / window) * 100 });
  }
  return out.slice(-points);
}

export function rMultipleSpark(closed: ClosedTrade[], window = 10, points = 14): SparkPoint[] {
  const final = closed
    .filter((t) => !t.partial && typeof t.pnl_pct === "number")
    .sort((a, b) => (a.exit_date ?? "").localeCompare(b.exit_date ?? ""));
  if (final.length < window) return [];
  const out: SparkPoint[] = [];
  for (let i = window - 1; i < final.length; i++) {
    const w = final.slice(i - window + 1, i + 1);
    const wins = w.filter((t) => (t.pnl_pct ?? 0) > 0);
    const losses = w.filter((t) => (t.pnl_pct ?? 0) <= 0);
    const avgWin = wins.length
      ? wins.reduce((s, t) => s + (t.pnl_pct ?? 0), 0) / wins.length
      : 0;
    const avgLoss = losses.length
      ? losses.reduce((s, t) => s + (t.pnl_pct ?? 0), 0) / losses.length
      : 0;
    const r = avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : 0;
    out.push({ v: r });
  }
  return out.slice(-points);
}

export type Timeframe = "1D" | "1W" | "1M" | "1Y" | "ALL";

export function filterEquityByTimeframe(
  curve: EquityPoint[],
  tf: Timeframe,
): EquityPoint[] {
  if (tf === "ALL" || curve.length <= 1) return curve;

  // 1D uses a rolling 24h window against the full ISO ts (preserves HH:MM
  // granularity). 1W/1M/1Y compare on the YYYY-MM-DD prefix.
  const isIntraday = tf === "1D";
  let cutoffKey: string;
  let pointKey: (p: EquityPoint) => string;
  if (isIntraday) {
    cutoffKey = new Date(Date.now() - 24 * 3600_000).toISOString();
    pointKey = (p) => (p.ts === "now" ? "9999" : p.ts);
  } else {
    const days = tf === "1W" ? 7 : tf === "1M" ? 30 : 365;
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    cutoff.setHours(0, 0, 0, 0);
    cutoffKey = cutoff.toISOString().slice(0, 10);
    pointKey = (p) => (p.ts === "now" ? "9999" : p.date);
  }

  // Anchor: last point before the cutoff, so the line doesn't start at 0.
  let anchorIdx = -1;
  for (let i = 0; i < curve.length; i++) {
    const p = curve[i];
    if (p.ts === "start") {
      anchorIdx = i;
      continue;
    }
    if (p.ts === "now") continue;
    if (pointKey(p) < cutoffKey) anchorIdx = i;
    else break;
  }
  const inRange = curve.filter(
    (p) => p.ts !== "start" && pointKey(p) >= cutoffKey,
  );
  if (anchorIdx >= 0) {
    const anchor = curve[anchorIdx];
    const projected: EquityPoint = isIntraday
      ? { ...anchor, ts: cutoffKey, date: cutoffKey.slice(0, 10) }
      : { ...anchor, ts: cutoffKey, date: cutoffKey };
    return [projected, ...inRange];
  }
  return inRange.length > 0 ? inRange : curve.slice(-1);
}
