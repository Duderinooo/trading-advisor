import type {
  ClosedTrade,
  EquityPoint,
  HitStats,
  Portfolio,
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
