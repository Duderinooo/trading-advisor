import type {
  CalibrationBin,
  ClosedTrade,
  EquityPoint,
  GateAttribution,
  GateBlock,
  HitStats,
  MistakeTrendPoint,
  OpenTrade,
  Portfolio,
  ShockResult,
  ThesisDecayFlag,
} from "./types";

export function computeEquityCurve(p: Portfolio): EquityPoint[] {
  const sorted = [...p.closed_trades]
    .filter((t) => t.exit_date)
    .sort((a, b) => a.exit_date.localeCompare(b.exit_date));
  let equity = p.total_capital_eur;
  let peak = equity;
  const points: EquityPoint[] = [
    { date: "start", equity, peak, dd_pct: 0 },
  ];
  for (const t of sorted) {
    equity += Number(t.pnl_eur ?? 0);
    if (equity > peak) peak = equity;
    const dd_pct = peak > 0 ? ((peak - equity) / peak) * 100 : 0;
    points.push({
      date: t.exit_date.split(" ")[0],
      equity: Math.round(equity * 100) / 100,
      peak: Math.round(peak * 100) / 100,
      dd_pct: Math.round(dd_pct * 100) / 100,
    });
  }
  return points;
}

export function computeHitStats(closed: ClosedTrade[]): HitStats | null {
  if (closed.length < 3) return null;
  const final = closed.filter((t) => !t.partial);
  const wins = final.filter((t) => (t.pnl_pct ?? 0) > 0);
  const losses = final.filter((t) => (t.pnl_pct ?? 0) <= 0);
  const total = final.length;
  const avgWin =
    wins.length > 0
      ? wins.reduce((s, t) => s + (t.pnl_pct ?? 0), 0) / wins.length
      : 0;
  const avgLoss =
    losses.length > 0
      ? losses.reduce((s, t) => s + (t.pnl_pct ?? 0), 0) / losses.length
      : 0;
  const rMultiple = avgLoss !== 0 ? avgWin / Math.abs(avgLoss) : null;

  const byConv: Record<number, ClosedTrade[]> = {};
  for (const t of final) {
    if (typeof t.conviction === "number") {
      const c = Math.floor(t.conviction);
      (byConv[c] ??= []).push(t);
    }
  }
  const convBreakdown: HitStats["conv_breakdown"] = {};
  for (const [c, ts] of Object.entries(byConv)) {
    const w = ts.filter((t) => (t.pnl_pct ?? 0) > 0).length;
    convBreakdown[Number(c)] = {
      wins: w,
      total: ts.length,
      rate: ts.length ? Math.round((w / ts.length) * 1000) / 10 : 0,
    };
  }

  const streak = final
    .slice(-5)
    .map((t) => ((t.pnl_pct ?? 0) > 0 ? "W" : "L"))
    .join("");

  const totalPnlEur =
    Math.round(
      final.reduce((s, t) => s + Number(t.pnl_eur ?? 0), 0) * 100,
    ) / 100;

  const scored = final
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
    const haircut = Math.abs(bias) >= 0.05 ? Math.round(bias * 1000) / 1000 : 0;
    calibration = {
      n,
      avg_brier: Math.round(avgBrier * 10000) / 10000,
      avg_p_predicted: Math.round(avgPPred * 1000) / 1000,
      actual_win_rate: Math.round(actual * 1000) / 1000,
      bias: Math.round(bias * 1000) / 1000,
      haircut,
    };
  }

  const recentLosses = final
    .filter((t) => (t.pnl_pct ?? 0) <= 0)
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

export function currentEquity(p: Portfolio): number {
  let eq = p.total_capital_eur;
  for (const t of p.closed_trades) eq += Number(t.pnl_eur ?? 0);
  return Math.round(eq * 100) / 100;
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

    let severity: ThesisDecayFlag["severity"] = "info";
    if (heldDays >= holdMax) severity = "stale";
    else if (heldDays >= Math.floor(holdMax * 0.75)) severity = "warn";

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

export type Timeframe = "1D" | "1W" | "1M" | "1Y" | "ALL";

export function filterEquityByTimeframe(
  curve: EquityPoint[],
  tf: Timeframe,
): EquityPoint[] {
  if (tf === "ALL" || curve.length <= 1) return curve;
  const days = tf === "1D" ? 1 : tf === "1W" ? 7 : tf === "1M" ? 30 : 365;
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  cutoff.setHours(0, 0, 0, 0);
  const cutoffISO = cutoff.toISOString().slice(0, 10);

  // Find anchor: last point with date < cutoff so curve doesn't start at 0.
  let anchorIdx = -1;
  for (let i = 0; i < curve.length; i++) {
    const p = curve[i];
    if (p.date === "start") {
      anchorIdx = i;
      continue;
    }
    if (p.date < cutoffISO) anchorIdx = i;
    else break;
  }
  const inRange = curve.filter(
    (p) => p.date !== "start" && p.date >= cutoffISO,
  );
  if (anchorIdx >= 0) {
    return [{ ...curve[anchorIdx], date: cutoffISO }, ...inRange];
  }
  return inRange.length > 0 ? inRange : curve.slice(-1);
}
