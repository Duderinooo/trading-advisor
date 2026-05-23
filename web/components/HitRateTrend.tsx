"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type TrendPoint = {
  date: string;
  n: number;
  win_rate: number;
  avg_pnl_pct: number;
};

type Analytics = {
  hit_rate_trend?: { generated_at: string; data: TrendPoint[] } | null;
};

export default function HitRateTrend() {
  const [data, setData] = useState<TrendPoint[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/analytics", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const all = (await res.json()) as Analytics;
        setData(all.hit_rate_trend?.data ?? []);
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
  if (!data.length) {
    return (
      <div className="text-sm text-zinc-500">
        Brauche ≥30 Tage geschlossener Trades für Trend.
      </div>
    );
  }

  return (
    <div style={{ width: "100%", height: 240 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
          <XAxis dataKey="date" stroke="#71717a" fontSize={11} />
          <YAxis stroke="#71717a" fontSize={11} domain={[0, 100]} unit="%" />
          <Tooltip
            contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }}
            labelStyle={{ color: "#a1a1aa" }}
          />
          <Line
            type="monotone"
            dataKey="win_rate"
            stroke="#10b981"
            strokeWidth={2}
            dot={false}
            name="Win-Rate %"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
