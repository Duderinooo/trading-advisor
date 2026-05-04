"use client";

import { useMemo } from "react";
import type { ClosedTrade, OpenTrade, PaperPortfolio } from "@/lib/types";

type PaperPayload =
  | PaperPortfolio
  | {
      paper: false;
      open_trades: [];
      closed_trades: [];
      cash_eur: number;
      total_capital_eur: number;
    };

function isLoaded(p: PaperPayload): p is PaperPortfolio {
  return p.paper === true;
}

function nonPartialPnl(closed: ClosedTrade[]): number {
  return closed.reduce((acc, t) => acc + (t.pnl_eur ?? 0), 0);
}

function topSetups(closed: ClosedTrade[], n = 3) {
  const buckets = new Map<
    string,
    { setup: string; pnl: number; wins: number; total: number }
  >();
  for (const t of closed) {
    if (t.partial) continue;
    const key = t.setup_type ?? "untagged";
    const cur = buckets.get(key) ?? {
      setup: key,
      pnl: 0,
      wins: 0,
      total: 0,
    };
    cur.pnl += t.pnl_eur ?? 0;
    cur.total += 1;
    if ((t.pnl_pct ?? 0) > 0) cur.wins += 1;
    buckets.set(key, cur);
  }
  const arr = Array.from(buckets.values());
  const winners = [...arr].sort((a, b) => b.pnl - a.pnl).slice(0, n);
  const losers = [...arr].sort((a, b) => a.pnl - b.pnl).slice(0, n);
  return { winners, losers };
}

function bookEquity(
  cash: number,
  open: OpenTrade[],
  prices?: Record<string, number>,
): number {
  let mtm = cash;
  for (const t of open) {
    const live = prices?.[t.ticker];
    const px = typeof live === "number" ? live : t.entry_price;
    mtm += px * (t.shares ?? 0);
  }
  return mtm;
}

export default function TrainingPortfolioCard({
  paper,
  livePrices,
}: {
  paper: PaperPayload;
  livePrices?: Record<string, number>;
}) {
  const loaded = isLoaded(paper);

  const equity = useMemo(
    () => (loaded ? bookEquity(paper.cash_eur, paper.open_trades, livePrices) : 0),
    [loaded, paper, livePrices],
  );
  const realizedPnl = useMemo(
    () => (loaded ? nonPartialPnl(paper.closed_trades) : 0),
    [loaded, paper],
  );
  const realizedPct = useMemo(() => {
    if (!loaded) return 0;
    const start = paper.total_capital_eur || 1;
    return (realizedPnl / start) * 100;
  }, [loaded, paper, realizedPnl]);

  const closedFinal = useMemo(
    () => (loaded ? paper.closed_trades.filter((t) => !t.partial) : []),
    [loaded, paper],
  );
  const wins = closedFinal.filter((t) => (t.pnl_pct ?? 0) > 0).length;
  const winRate =
    closedFinal.length > 0 ? (wins / closedFinal.length) * 100 : 0;
  const avgPnl =
    closedFinal.length > 0
      ? closedFinal.reduce((a, t) => a + (t.pnl_pct ?? 0), 0) /
        closedFinal.length
      : 0;

  const setups = useMemo(() => topSetups(closedFinal), [closedFinal]);

  if (!loaded) {
    return (
      <div className="text-sm text-zinc-400">
        Trainings-Portfolio leer — wartet auf ersten Auto-Open.
      </div>
    );
  }

  const pnlTone =
    realizedPnl > 0
      ? "num-pos"
      : realizedPnl < 0
        ? "num-neg"
        : "";

  return (
    <div className="space-y-3 text-sm">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          label="Equity"
          value={`${equity.toFixed(2)} €`}
          sub={`cash ${paper.cash_eur.toFixed(2)} €`}
        />
        <Stat
          label="Realized P&L"
          value={`${realizedPnl >= 0 ? "+" : ""}${realizedPnl.toFixed(2)} €`}
          sub={`${realizedPct >= 0 ? "+" : ""}${realizedPct.toFixed(2)}% vs start`}
          tone={pnlTone}
        />
        <Stat
          label="Open"
          value={`${paper.open_trades.length}`}
          sub={
            paper.open_trades.length
              ? paper.open_trades.map((t) => t.ticker).join(" · ")
              : "—"
          }
        />
        <Stat
          label="Closed"
          value={`${closedFinal.length}`}
          sub={
            closedFinal.length
              ? `WR ${winRate.toFixed(0)}% · avg ${avgPnl >= 0 ? "+" : ""}${avgPnl.toFixed(2)}%`
              : "—"
          }
        />
      </div>

      {paper.open_trades.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {paper.open_trades.map((t) => {
            const live = livePrices?.[t.ticker];
            const px = typeof live === "number" ? live : t.entry_price;
            const entry = t.entry_price || 0;
            const pct = entry ? ((px - entry) / entry) * 100 : 0;
            const tone =
              pct > 0
                ? "border-emerald-700/60 text-emerald-300"
                : pct < 0
                  ? "border-rose-700/60 text-rose-300"
                  : "border-zinc-700 text-zinc-300";
            return (
              <span
                key={t.ticker}
                className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs ${tone}`}
              >
                {t.ticker}
                <span className="text-zinc-500">·</span>
                {pct >= 0 ? "+" : ""}
                {pct.toFixed(2)}%
              </span>
            );
          })}
        </div>
      )}

      {closedFinal.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <SetupList title="Top winners" rows={setups.winners} positive />
          <SetupList title="Top losers" rows={setups.losers} positive={false} />
        </div>
      )}

      <div className="text-xs text-zinc-500">
        Started {paper.started_at ?? "—"} · €1/Seite Fee · Paper, keine Real-
        Execution
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
}) {
  return (
    <div className="rounded border border-zinc-900/60 bg-zinc-950/40 px-3 py-2">
      <div className="text-xs uppercase tracking-wide text-zinc-500">
        {label}
      </div>
      <div className={`text-lg font-medium ${tone ?? ""}`}>{value}</div>
      {sub && <div className="text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

function SetupList({
  title,
  rows,
  positive,
}: {
  title: string;
  rows: { setup: string; pnl: number; wins: number; total: number }[];
  positive: boolean;
}) {
  if (!rows.length) return null;
  return (
    <div className="rounded border border-zinc-900/60 bg-zinc-950/40 px-3 py-2">
      <div className="mb-1 text-xs uppercase tracking-wide text-zinc-500">
        {title}
      </div>
      <ul className="space-y-1">
        {rows.map((r) => {
          const cls = positive
            ? r.pnl > 0
              ? "num-pos"
              : "text-zinc-500"
            : r.pnl < 0
              ? "num-neg"
              : "text-zinc-500";
          return (
            <li
              key={r.setup}
              className="flex items-center justify-between text-sm"
            >
              <span className="text-zinc-300">{r.setup}</span>
              <span className={cls}>
                {r.pnl >= 0 ? "+" : ""}
                {r.pnl.toFixed(2)} € · {r.wins}/{r.total}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
