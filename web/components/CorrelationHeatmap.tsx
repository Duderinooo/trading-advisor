"use client";

import type { CorrelationMatrix } from "@/lib/types";

function colorFor(c: number | null): string {
  if (c === null || Number.isNaN(c)) return "bg-zinc-900 text-zinc-700";
  if (c >= 0.85) return "bg-rose-700/80 text-zinc-100";
  if (c >= 0.7) return "bg-rose-600/60 text-zinc-100";
  if (c >= 0.5) return "bg-amber-600/50 text-zinc-100";
  if (c >= 0.3) return "bg-amber-700/30 text-zinc-200";
  if (c >= 0) return "bg-zinc-800 text-zinc-300";
  if (c >= -0.3) return "bg-emerald-900/40 text-zinc-200";
  return "bg-emerald-700/60 text-zinc-100";
}

export default function CorrelationHeatmap({
  matrix,
}: {
  matrix?: CorrelationMatrix | null;
}) {
  if (!matrix || matrix.tickers.length < 2) {
    return (
      <div className="text-sm text-zinc-500">
        Min. 2 offene Positionen für Korrelations-Snapshot. Snapshot wird beim
        Morning-Briefing aktualisiert.
      </div>
    );
  }
  const { tickers, matrix: m, lookback_days, computed_at } = matrix;

  return (
    <div className="space-y-2">
      <div className="text-xs text-zinc-500">
        {lookback_days}d Daily-Returns · Snapshot {computed_at}
      </div>
      <div className="overflow-x-auto">
        <table className="text-xs border-separate border-spacing-1">
          <thead>
            <tr>
              <th className="w-16" />
              {tickers.map((t) => (
                <th
                  key={t}
                  className="px-2 py-1 text-zinc-400 font-medium text-left"
                >
                  {t}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tickers.map((row) => (
              <tr key={row}>
                <th className="pr-2 text-zinc-400 font-medium text-right">
                  {row}
                </th>
                {tickers.map((col) => {
                  const c = m[row]?.[col];
                  const v = typeof c === "number" ? c : null;
                  const cls = colorFor(v);
                  return (
                    <td
                      key={col}
                      className={`px-2 py-1 text-center rounded tabular-nums ${cls}`}
                    >
                      {v === null ? "—" : v.toFixed(2)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-3 text-[10px] text-zinc-500">
        <span><span className="inline-block w-2 h-2 mr-1 align-middle bg-rose-700/80 rounded" />≥0.85 cluster</span>
        <span><span className="inline-block w-2 h-2 mr-1 align-middle bg-rose-600/60 rounded" />≥0.7 high</span>
        <span><span className="inline-block w-2 h-2 mr-1 align-middle bg-amber-600/50 rounded" />≥0.5</span>
        <span><span className="inline-block w-2 h-2 mr-1 align-middle bg-emerald-700/60 rounded" />negativ</span>
      </div>
    </div>
  );
}
