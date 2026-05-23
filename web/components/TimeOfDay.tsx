"use client";

import { useEffect, useState } from "react";

type Bucket = { wins: number; total: number; rate: number; avg_pnl_pct: number };
type Buckets = Record<string, Bucket>;

type Analytics = {
  time_of_day?: { generated_at: string; data: Buckets } | null;
};

const ORDER = ["open", "morning", "midday", "us_open", "late"] as const;
const LABEL: Record<(typeof ORDER)[number], string> = {
  open: "Open (09-10)",
  morning: "Morning (10-12)",
  midday: "Midday (12-15)",
  us_open: "US Open (15-17)",
  late: "Late (17-22)",
};

export default function TimeOfDay() {
  const [data, setData] = useState<Buckets | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/analytics", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const all = (await res.json()) as Analytics;
        setData(all.time_of_day?.data ?? {});
      } catch (e) {
        setErr((e as Error).message);
      }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (err) return <div className="text-sm text-rose-400">Load failed: {err}</div>;
  if (!data) return <div className="text-sm text-zinc-500">Lade…</div>;
  const present = ORDER.filter((k) => data[k]?.total);
  if (!present.length) {
    return (
      <div className="text-sm text-zinc-500">
        Brauche ≥1 geschlossenen Trade mit entry_date.
      </div>
    );
  }

  return (
    <table className="w-full text-sm">
      <thead className="text-zinc-500 text-xs uppercase tracking-wide">
        <tr>
          <th className="text-left py-1">Bucket</th>
          <th className="text-right py-1">N</th>
          <th className="text-right py-1">Win-Rate</th>
          <th className="text-right py-1">Avg PnL</th>
        </tr>
      </thead>
      <tbody>
        {present.map((k) => {
          const b = data[k];
          const isExpectancyPos = b.avg_pnl_pct >= 0;
          return (
            <tr key={k} className="border-t border-zinc-800/50">
              <td className="py-1.5">{LABEL[k]}</td>
              <td className="py-1.5 text-right text-zinc-400">{b.total}</td>
              <td className="py-1.5 text-right">{b.rate.toFixed(0)}%</td>
              <td
                className={`py-1.5 text-right font-mono ${
                  isExpectancyPos ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                {isExpectancyPos ? "+" : ""}
                {b.avg_pnl_pct.toFixed(2)}%
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
