"use client";

import { filterEquityByTimeframe, type Timeframe } from "@/lib/compute";
import type { EquityPoint } from "@/lib/types";
import { format, parseISO } from "date-fns";
import { de } from "date-fns/locale";
import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const TFS: Timeframe[] = ["1D", "1W", "1M", "1Y", "ALL"];

const COLOR_UP = "#34d399";
const COLOR_DOWN = "#fb7185";
const COLOR_DIM = "#71717a";
const COLOR_GRID = "#1d1d22";
const COLOR_CURSOR = "#52525b";
const COLOR_REF = "#52525b";

const eur0 = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
});
const eur2 = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const eur2Signed = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});
const pct2Signed = new Intl.NumberFormat("de-DE", {
  style: "percent",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});

function isSentinel(ts: string): ts is "start" | "now" {
  return ts === "start" || ts === "now";
}

function safeParse(ts: string): Date | null {
  if (isSentinel(ts)) return null;
  try {
    const d = parseISO(ts);
    return Number.isNaN(d.getTime()) ? null : d;
  } catch {
    return null;
  }
}

function formatTick(ts: string, tf: Timeframe): string {
  const d = safeParse(ts);
  if (!d) return "";
  if (tf === "1D") return format(d, "HH:mm");
  if (tf === "1W") return format(d, "EE HH:mm", { locale: de });
  if (tf === "1M") return format(d, "dd.MM");
  return format(d, "MMM yy", { locale: de });
}

function formatTooltipLabel(ts: string): string {
  if (ts === "now") return "jetzt";
  if (ts === "start") return "Start";
  const d = safeParse(ts);
  if (!d) return ts;
  return format(d, "EEE dd.MM. HH:mm", { locale: de });
}

type CursorProps = {
  points?: { x: number; y: number }[];
  top?: number;
  height?: number;
};

function CustomCursor({ points, top, height }: CursorProps) {
  if (!points || points.length === 0) return null;
  const x = points[0].x;
  const y1 = top ?? 0;
  const y2 = (top ?? 0) + (height ?? 0);
  return (
    <line
      x1={x}
      x2={x}
      y1={y1}
      y2={y2}
      stroke={COLOR_CURSOR}
      strokeDasharray="3 3"
      strokeWidth={1}
    />
  );
}

type TooltipProps = {
  active?: boolean;
  payload?: Array<{ payload: EquityPoint }>;
  startEq: number;
  lineColor: string;
};

function CustomTooltip({ active, payload, startEq, lineColor }: TooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0]?.payload;
  if (!p) return null;
  const delta = p.equity - startEq;
  const pct = startEq > 0 ? delta / startEq : 0;
  const deltaColor = delta >= 0 ? COLOR_UP : COLOR_DOWN;
  return (
    <div
      style={{
        background: "#18181c",
        border: "1px solid #26262c",
        borderRadius: 8,
        padding: "8px 10px",
        fontSize: 12,
        color: "#e4e4e7",
        boxShadow: "0 4px 16px rgba(0,0,0,0.4)",
        minWidth: 160,
      }}
    >
      <div style={{ color: "#a1a1aa", fontSize: 11, marginBottom: 4 }}>
        {formatTooltipLabel(p.ts)}
      </div>
      <div style={{ fontWeight: 600, fontSize: 14, color: lineColor }}>
        {eur2.format(p.equity)}
      </div>
      <div style={{ color: deltaColor, marginTop: 2 }}>
        {eur2Signed.format(delta)} ({pct2Signed.format(pct)})
      </div>
      {p.dd_pct > 0.05 && (
        <div style={{ color: "#a1a1aa", marginTop: 2 }}>
          Drawdown: -{p.dd_pct.toFixed(2)} %
        </div>
      )}
    </div>
  );
}

export default function EquityChart({ data }: { data: EquityPoint[] }) {
  const [tf, setTf] = useState<Timeframe>("1D");
  const filtered = useMemo(() => filterEquityByTimeframe(data, tf), [data, tf]);

  const startEq = filtered[0]?.equity ?? 0;
  const endEq = filtered[filtered.length - 1]?.equity ?? 0;
  const change = endEq - startEq;
  const pct = startEq > 0 ? change / startEq : 0;
  const up = change >= 0;
  const lineColor = up ? COLOR_UP : COLOR_DOWN;

  const stats = useMemo(() => {
    if (filtered.length === 0) {
      return { min: 0, max: 0, minPt: null, maxPt: null };
    }
    let minPt = filtered[0];
    let maxPt = filtered[0];
    for (const p of filtered) {
      if (p.equity < minPt.equity) minPt = p;
      if (p.equity > maxPt.equity) maxPt = p;
    }
    return { min: minPt.equity, max: maxPt.equity, minPt, maxPt };
  }, [filtered]);

  const lastPt = filtered[filtered.length - 1] ?? null;
  const rangeRel = stats.max > 0 ? (stats.max - stats.min) / stats.max : 0;
  const showMinMax =
    filtered.length >= 8 &&
    rangeRel >= 0.02 &&
    stats.minPt !== null &&
    stats.maxPt !== null &&
    stats.minPt !== stats.maxPt;

  const yPadFor = (min: number, max: number) => Math.max((max - min) * 0.05, 1);

  return (
    <div>
      <div className="flex items-end justify-between mb-3 gap-3 flex-wrap">
        <div>
          <div className="text-2xl font-semibold text-zinc-100 tabular-nums">
            {eur2.format(endEq)}
          </div>
          <div className="text-sm mt-0.5">
            <span className={up ? "text-emerald-400" : "text-rose-400"}>
              {eur2Signed.format(change)} ({pct2Signed.format(pct)})
            </span>
            <span className="text-zinc-500 ml-2 text-xs">· {tf}</span>
          </div>
        </div>
        <div className="flex gap-1">
          {TFS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTf(t)}
              className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
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
            margin={{ top: 16, right: 24, left: 8, bottom: 0 }}
          >
            <defs>
              <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={lineColor} stopOpacity={0.32} />
                <stop offset="100%" stopColor={lineColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid
              stroke={COLOR_GRID}
              strokeDasharray="2 4"
              vertical={false}
            />
            <XAxis
              dataKey="ts"
              type="category"
              tick={{ fill: COLOR_DIM, fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              interval="preserveStartEnd"
              minTickGap={48}
              tickFormatter={(v: string) => formatTick(v, tf)}
            />
            <YAxis
              width={64}
              tick={{ fill: COLOR_DIM, fontSize: 12 }}
              tickLine={false}
              axisLine={false}
              tickCount={5}
              domain={[
                (min: number) => Math.floor(min - yPadFor(min, stats.max)),
                (max: number) => Math.ceil(max + yPadFor(stats.min, max)),
              ]}
              tickFormatter={(v: number) => eur0.format(v)}
            />
            <ReferenceLine
              y={startEq}
              stroke={COLOR_REF}
              strokeDasharray="3 4"
              strokeWidth={1}
              ifOverflow="extendDomain"
            />
            <Tooltip
              cursor={<CustomCursor />}
              content={
                <CustomTooltip startEq={startEq} lineColor={lineColor} />
              }
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={lineColor}
              strokeWidth={2}
              fill="url(#eqFill)"
              isAnimationActive={false}
              activeDot={{
                r: 4,
                strokeWidth: 2,
                stroke: "#18181c",
                fill: lineColor,
              }}
              dot={false}
            />
            {showMinMax && stats.minPt && (
              <ReferenceDot
                x={stats.minPt.ts}
                y={stats.minPt.equity}
                r={3}
                fill={COLOR_DIM}
                stroke="none"
                label={{
                  value: eur0.format(stats.minPt.equity),
                  position: "bottom",
                  fill: COLOR_DIM,
                  fontSize: 10,
                }}
              />
            )}
            {showMinMax && stats.maxPt && (
              <ReferenceDot
                x={stats.maxPt.ts}
                y={stats.maxPt.equity}
                r={3}
                fill={COLOR_DIM}
                stroke="none"
                label={{
                  value: eur0.format(stats.maxPt.equity),
                  position: "top",
                  fill: COLOR_DIM,
                  fontSize: 10,
                }}
              />
            )}
            {lastPt && (
              <ReferenceDot
                x={lastPt.ts}
                y={lastPt.equity}
                r={5}
                fill={lineColor}
                stroke="#18181c"
                strokeWidth={2}
                shape={(props: { cx?: number; cy?: number }) => (
                  <g>
                    <circle
                      cx={props.cx}
                      cy={props.cy}
                      r={6}
                      fill={lineColor}
                      opacity={0.25}
                    >
                      <animate
                        attributeName="r"
                        values="6;14;6"
                        dur="2s"
                        repeatCount="indefinite"
                      />
                      <animate
                        attributeName="opacity"
                        values="0.35;0;0.35"
                        dur="2s"
                        repeatCount="indefinite"
                      />
                    </circle>
                    <circle
                      cx={props.cx}
                      cy={props.cy}
                      r={4}
                      fill={lineColor}
                      stroke="#18181c"
                      strokeWidth={2}
                    />
                  </g>
                )}
              />
            )}
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
