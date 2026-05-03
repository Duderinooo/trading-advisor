"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ClosedTrade } from "@/lib/types";
import { computeMistakeTrend } from "@/lib/compute";

const COLORS = {
  prediction: "#f43f5e",
  timing: "#f59e0b",
  execution: "#3b82f6",
  external: "#8b5cf6",
  untagged: "#52525b",
};

export default function MistakeTrend({
  closed,
}: {
  closed: ClosedTrade[];
}) {
  const trend = computeMistakeTrend(closed, 10);

  if (trend.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        ≥10 Loss-Trades nötig für rolling Trend (currently{" "}
        {closed.filter((t) => !t.partial && (t.pnl_pct ?? 0) <= 0).length}).
      </div>
    );
  }

  // Convert raw counts to share-of-window for stacked %.
  const data = trend.map((p) => ({
    bucket: p.bucket,
    prediction: p.prediction / p.total,
    timing: p.timing / p.total,
    execution: p.execution / p.total,
    external: p.external / p.total,
    untagged: p.untagged / p.total,
  }));

  return (
    <div className="space-y-2">
      <div className="text-xs text-zinc-500">
        Rolling 10-Loss-Window. Y = Anteil pro Klasse. Hilft sehen, ob
        Param-Änderungen einen Mistake-Bias verschoben haben.
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart
          data={data}
          margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
        >
          <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
          <XAxis dataKey="bucket" stroke="#71717a" fontSize={11} />
          <YAxis
            stroke="#71717a"
            fontSize={11}
            domain={[0, 1]}
            tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={{
              background: "#18181b",
              border: "1px solid #3f3f46",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#a1a1aa" }}
            formatter={(v, name) => [
              `${(Number(v) * 100).toFixed(0)}%`,
              String(name),
            ]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Area
            isAnimationActive={false}
            type="monotone"
            dataKey="prediction"
            stackId="1"
            stroke={COLORS.prediction}
            fill={COLORS.prediction}
          />
          <Area
            isAnimationActive={false}
            type="monotone"
            dataKey="timing"
            stackId="1"
            stroke={COLORS.timing}
            fill={COLORS.timing}
          />
          <Area
            isAnimationActive={false}
            type="monotone"
            dataKey="execution"
            stackId="1"
            stroke={COLORS.execution}
            fill={COLORS.execution}
          />
          <Area
            isAnimationActive={false}
            type="monotone"
            dataKey="external"
            stackId="1"
            stroke={COLORS.external}
            fill={COLORS.external}
          />
          <Area
            isAnimationActive={false}
            type="monotone"
            dataKey="untagged"
            stackId="1"
            stroke={COLORS.untagged}
            fill={COLORS.untagged}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
