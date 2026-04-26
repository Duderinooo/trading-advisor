"use client";

import { useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EquityPoint } from "@/lib/types";
import { filterEquityByTimeframe, type Timeframe } from "@/lib/compute";

const TFS: Timeframe[] = ["1D", "1W", "1M", "1Y", "ALL"];

export default function EquityChart({ data }: { data: EquityPoint[] }) {
  const [tf, setTf] = useState<Timeframe>("ALL");
  const filtered = filterEquityByTimeframe(data, tf);

  const startEq = filtered[0]?.equity ?? 0;
  const endEq = filtered[filtered.length - 1]?.equity ?? 0;
  const change = endEq - startEq;
  const pct = startEq > 0 ? (change / startEq) * 100 : 0;
  const up = change >= 0;

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm">
          <span className={up ? "text-emerald-400" : "text-rose-400"}>
            {up ? "+" : ""}
            {change.toFixed(2)} € ({up ? "+" : ""}
            {pct.toFixed(2)}%)
          </span>
          <span className="text-zinc-500 ml-2 text-xs">{tf} window</span>
        </div>
        <div className="flex gap-1">
          {TFS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTf(t)}
              className={`px-2 py-1 text-xs rounded border transition-colors ${
                tf === t
                  ? "bg-zinc-100 text-zinc-900 border-zinc-100"
                  : "bg-zinc-900 text-zinc-400 border-zinc-800 hover:border-zinc-700 hover:text-zinc-200"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      {filtered.length < 2 ? (
        <div className="text-sm text-zinc-500 h-[280px] flex items-center justify-center">
          Keine Datenpunkte im Zeitraum.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart
            data={filtered}
            margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
          >
            <defs>
              <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="0%"
                  stopColor={up ? "#22c55e" : "#f43f5e"}
                  stopOpacity={0.4}
                />
                <stop
                  offset="100%"
                  stopColor={up ? "#22c55e" : "#f43f5e"}
                  stopOpacity={0}
                />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
            <XAxis dataKey="date" stroke="#71717a" fontSize={11} />
            <YAxis
              stroke="#71717a"
              fontSize={11}
              domain={["dataMin - 20", "dataMax + 20"]}
              tickFormatter={(v) => `${Math.round(Number(v))}€`}
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
                const key = String(name);
                if (key === "dd_pct") return [`${num}%`, "Drawdown"];
                return [`${num}€`, key === "equity" ? "Equity" : "Peak"];
              }}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={up ? "#22c55e" : "#f43f5e"}
              fill="url(#eqFill)"
              strokeWidth={2}
            />
            <Area
              type="monotone"
              dataKey="peak"
              stroke="#52525b"
              strokeDasharray="3 3"
              fill="none"
              strokeWidth={1}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
