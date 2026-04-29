"use client";

import { useEffect, useState } from "react";

type Gate = {
  evaluated: number;
  would_block: number;
  would_block_winners: number;
  would_block_losers: number;
};

type Report = {
  n_trades: number;
  n_winners: number;
  n_losers: number;
  gates: Record<string, Gate>;
};

export default function BacktestReport() {
  const [rep, setRep] = useState<Report | null | undefined>(undefined);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/backtest", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as Report | null;
        setRep(data);
      } catch (e) {
        setErr((e as Error).message);
      }
    };
    load();
  }, []);

  if (err) return <div className="text-sm text-rose-400">Load failed: {err}</div>;
  if (rep === undefined) return <div className="text-sm text-zinc-500">Lade…</div>;
  if (rep === null) {
    return (
      <div className="text-sm text-zinc-500 space-y-2">
        <div>Kein Backtest-Report vorhanden.</div>
        <code className="block text-xs font-mono text-zinc-300 bg-zinc-900 px-2 py-1 rounded">
          ./venv/bin/python -m core.backtest --write
        </code>
      </div>
    );
  }

  const entries = Object.entries(rep.gates);
  if (entries.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        Replay durchgelaufen ({rep.n_trades} Trades), aber keine alten Trades hatten
        Gate-Daten. Sobald neue Recommendations geschlossen werden, füllt sich der Report.
      </div>
    );
  }

  return (
    <div className="space-y-3 text-sm">
      <div className="text-xs text-zinc-500">
        {rep.n_trades} Trades replayed (W {rep.n_winners} / L {rep.n_losers}).
        Net &gt; 0 = Gate zahlt Miete (mehr Loser geblockt als Winner).
      </div>
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-zinc-500 text-left">
            <th className="pb-1">Gate</th>
            <th className="pb-1 text-right">Eval</th>
            <th className="pb-1 text-right">Block</th>
            <th className="pb-1 text-right text-rose-400">BlockW</th>
            <th className="pb-1 text-right text-emerald-400">BlockL</th>
            <th className="pb-1 text-right">Net</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([g, s]) => {
            const net = s.would_block_losers - s.would_block_winners;
            const tone =
              net > 0
                ? "text-emerald-400"
                : net < 0
                  ? "text-rose-400"
                  : "text-zinc-400";
            return (
              <tr key={g} className="border-t border-zinc-800">
                <td className="py-1 text-zinc-300">{g}</td>
                <td className="py-1 text-right text-zinc-400">{s.evaluated}</td>
                <td className="py-1 text-right text-zinc-300">{s.would_block}</td>
                <td className="py-1 text-right text-rose-400">{s.would_block_winners}</td>
                <td className="py-1 text-right text-emerald-400">{s.would_block_losers}</td>
                <td className={`py-1 text-right font-semibold ${tone}`}>
                  {net > 0 ? "+" : ""}
                  {net}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
