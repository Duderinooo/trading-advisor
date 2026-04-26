"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Portfolio } from "@/lib/types";
import {
  computeEquityCurve,
  computeHitStats,
  currentEquity,
  openExposure,
} from "@/lib/compute";
import { Card, Kpi } from "@/components/Card";
import EquityChart from "@/components/EquityChart";
import OpenTrades from "@/components/OpenTrades";
import ClosedTrades from "@/components/ClosedTrades";
import WatchLevels from "@/components/WatchLevels";
import Stats from "@/components/Stats";

const REFRESH_MS = 30_000;

export default function Dashboard({ initial }: { initial: Portfolio }) {
  const [portfolio, setPortfolio] = useState<Portfolio>(initial);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  useEffect(() => {
    setRefreshedAt(new Date());
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await fetch("/api/portfolio", { cache: "no-store" });
      if (res.ok) {
        const data = (await res.json()) as Portfolio;
        setPortfolio(data);
        setRefreshedAt(new Date());
      }
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [autoRefresh, refresh]);

  const equity = useMemo(() => currentEquity(portfolio), [portfolio]);
  const exposure = useMemo(() => openExposure(portfolio), [portfolio]);
  const curve = useMemo(() => computeEquityCurve(portfolio), [portfolio]);
  const stats = useMemo(
    () => computeHitStats(portfolio.closed_trades),
    [portfolio.closed_trades],
  );
  const peak = curve.length ? curve[curve.length - 1].peak : equity;
  const dd = curve.length ? curve[curve.length - 1].dd_pct : 0;
  const totalReturn =
    portfolio.total_capital_eur > 0
      ? ((equity - portfolio.total_capital_eur) /
          portfolio.total_capital_eur) *
        100
      : 0;

  const ddTone: "neutral" | "good" | "warn" | "bad" =
    dd === 0 ? "good" : dd < 5 ? "neutral" : dd < 10 ? "warn" : "bad";
  const retTone: "neutral" | "good" | "bad" =
    totalReturn > 0 ? "good" : totalReturn < 0 ? "bad" : "neutral";

  return (
    <>
      <header className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-lg font-semibold">Trading Advisor</h1>
          <p className="text-xs text-zinc-500">
            Last update: {portfolio.last_updated ?? "—"} · Last analysis:{" "}
            {portfolio.last_analysis ?? "—"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <a
            href="/logs"
            className="text-xs px-3 py-1.5 rounded border border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          >
            Logs →
          </a>
          {portfolio.kill_switch_active && (
            <span className="px-2 py-1 text-xs rounded bg-rose-900/40 text-rose-300 border border-rose-800">
              KILL-SWITCH
            </span>
          )}
          {portfolio.dd_halt_active && (
            <span className="px-2 py-1 text-xs rounded bg-amber-900/40 text-amber-300 border border-amber-800">
              DD-HALT
            </span>
          )}
          <label className="flex items-center gap-2 text-xs text-zinc-500 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="accent-emerald-500"
            />
            Auto 30s
          </label>
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="text-xs px-3 py-1.5 rounded border border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-700 disabled:opacity-50"
          >
            {refreshing ? "…" : "↻"}{" "}
            <span suppressHydrationWarning>
              {refreshedAt ? refreshedAt.toLocaleTimeString() : "—"}
            </span>
          </button>
        </div>
      </header>

      <main className="p-6 space-y-6 max-w-7xl mx-auto">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Kpi
            label="Equity"
            value={`${equity.toFixed(2)} €`}
            sub={`Start ${portfolio.total_capital_eur.toFixed(0)} €`}
          />
          <Kpi
            label="Total Return"
            value={`${totalReturn >= 0 ? "+" : ""}${totalReturn.toFixed(2)}%`}
            tone={retTone}
            sub={`Peak ${peak.toFixed(2)} €`}
          />
          <Kpi
            label="Drawdown"
            value={`${dd.toFixed(2)}%`}
            tone={ddTone}
            sub="vs Peak"
          />
          <Kpi
            label="Cash / Exposure"
            value={`${portfolio.cash_eur.toFixed(0)} € / ${exposure.toFixed(0)} €`}
            sub={`${portfolio.open_trades.length} offene Position(en)`}
          />
        </div>

        <Card title="Equity Curve">
          <EquityChart data={curve} />
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card title="Hit Stats" className="lg:col-span-1">
            <Stats stats={stats} />
          </Card>
          <Card title="Watch Levels" className="lg:col-span-2">
            <WatchLevels levels={portfolio.watch_levels} />
          </Card>
        </div>

        <Card title="Open Trades">
          <OpenTrades trades={portfolio.open_trades} />
        </Card>

        <Card title="Closed Trades (recent 25)">
          <ClosedTrades trades={portfolio.closed_trades} />
        </Card>
      </main>
    </>
  );
}
