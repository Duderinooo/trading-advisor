"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ComposedChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CalibrationBin } from "@/lib/types";

export default function CalibrationCurve() {
  const [bins, setBins] = useState<CalibrationBin[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/calibration-bins", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        setBins((await res.json()) as CalibrationBin[]);
      } catch (e) {
        setErr((e as Error).message);
      }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (err) return <div className="text-sm text-rose-400">Load failed: {err}</div>;
  if (!bins) return <div className="text-sm text-zinc-500">Lade…</div>;

  const filled = bins.filter((b) => b.n > 0);
  if (filled.length < 2) {
    return (
      <div className="text-sm text-zinc-500">
        Min. 2 nicht-leere Bins nötig (≥4 Trades mit p_win + outcome).
      </div>
    );
  }

  const data = filled.map((b) => ({
    predicted: b.predicted,
    actual: b.actual,
    n: b.n,
    range: b.range,
  }));

  return (
    <div className="space-y-2">
      <div className="text-xs text-zinc-500">
        Punkt unter Diagonale = überschätzt (overconfident). Über Diagonale = unterschätzt.
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart
          data={data}
          margin={{ top: 8, right: 16, left: 0, bottom: 4 }}
        >
          <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
          <XAxis
            dataKey="predicted"
            type="number"
            domain={[0, 1]}
            stroke="#71717a"
            fontSize={11}
            tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`}
            label={{
              value: "Predicted p_win",
              position: "insideBottom",
              offset: -2,
              fill: "#71717a",
              fontSize: 11,
            }}
          />
          <YAxis
            dataKey="actual"
            type="number"
            domain={[0, 1]}
            stroke="#71717a"
            fontSize={11}
            tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`}
            label={{
              value: "Actual",
              angle: -90,
              position: "insideLeft",
              fill: "#71717a",
              fontSize: 11,
            }}
          />
          <Tooltip
            contentStyle={{
              background: "#18181b",
              border: "1px solid #3f3f46",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#a1a1aa" }}
            formatter={(v, name) => {
              const num = Number(v ?? 0);
              if (name === "actual" || name === "predicted")
                return [`${(num * 100).toFixed(1)}%`, name];
              return [num, name];
            }}
          />
          <ReferenceLine
            stroke="#52525b"
            strokeDasharray="3 3"
            segment={[
              { x: 0, y: 0 },
              { x: 1, y: 1 },
            ]}
          />
          <Line
            type="monotone"
            dataKey="actual"
            stroke="#22c55e"
            strokeWidth={2}
            dot={{ fill: "#22c55e", r: 4 }}
          />
          <Scatter dataKey="actual" fill="#22c55e" />
        </ComposedChart>
      </ResponsiveContainer>
      <ul className="space-y-1 text-xs text-zinc-400">
        {data.map((d) => (
          <li
            key={d.range}
            className="flex justify-between font-mono"
          >
            <span className="text-zinc-500">{d.range}</span>
            <span>
              pred {(d.predicted * 100).toFixed(1)}% → actual{" "}
              {(d.actual * 100).toFixed(1)}% (n={d.n})
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
