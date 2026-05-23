"use client";

import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type TrajPoint = { date: string; equity: number; peak: number; dd_pct: number };

type DDPayload = {
  starting_eur: number;
  equity_eur: number;
  peak_eur: number;
  current_dd_pct: number;
  current_dd_eur: number;
  soft_threshold_pct: number;
  halt_threshold_pct: number;
  soft_distance_pct: number;
  halt_distance_pct: number;
  soft_active: boolean;
  halt_active: boolean;
  trajectory: TrajPoint[];
};

type Analytics = {
  drawdown_trajectory?: { generated_at: string; data: DDPayload } | null;
};

export default function DrawdownTrajectory() {
  const [data, setData] = useState<DDPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/analytics", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const all = (await res.json()) as Analytics;
        setData(all.drawdown_trajectory?.data ?? null);
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
  if (!data.trajectory.length) {
    return (
      <div className="text-sm text-zinc-500">
        Brauche ≥1 closed trade für Trajectory.
      </div>
    );
  }

  return (
    <div>
      <div className="flex gap-4 mb-3 text-sm">
        <div>
          <span className="text-zinc-500">Now:</span>{" "}
          <span
            className={
              data.halt_active
                ? "text-rose-400 font-semibold"
                : data.soft_active
                  ? "text-amber-400"
                  : "text-emerald-400"
            }
          >
            -{data.current_dd_pct.toFixed(2)}%
          </span>
        </div>
        <div>
          <span className="text-zinc-500">Halt @</span>{" "}
          {data.halt_threshold_pct}% (Δ {data.halt_distance_pct.toFixed(1)}%)
        </div>
        <div>
          <span className="text-zinc-500">Soft @</span>{" "}
          {data.soft_threshold_pct}% (Δ {data.soft_distance_pct.toFixed(1)}%)
        </div>
      </div>
      <div style={{ width: "100%", height: 200 }}>
        <ResponsiveContainer>
          <AreaChart
            data={data.trajectory}
            margin={{ top: 10, right: 20, left: 0, bottom: 10 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
            <XAxis dataKey="date" stroke="#71717a" fontSize={11} />
            <YAxis stroke="#71717a" fontSize={11} unit="%" />
            <Tooltip
              contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }}
              labelStyle={{ color: "#a1a1aa" }}
            />
            <ReferenceLine
              y={data.soft_threshold_pct}
              stroke="#f59e0b"
              strokeDasharray="3 3"
              label={{ value: "soft", fill: "#f59e0b", fontSize: 10 }}
            />
            <ReferenceLine
              y={data.halt_threshold_pct}
              stroke="#f43f5e"
              strokeDasharray="3 3"
              label={{ value: "halt", fill: "#f43f5e", fontSize: 10 }}
            />
            <Area
              type="monotone"
              dataKey="dd_pct"
              stroke="#f43f5e"
              fill="#f43f5e"
              fillOpacity={0.2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
