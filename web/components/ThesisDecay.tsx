"use client";

import type { OpenTrade } from "@/lib/types";
import { computeThesisDecay } from "@/lib/compute";

export default function ThesisDecay({ open }: { open: OpenTrade[] }) {
  const flags = computeThesisDecay(open);

  if (flags.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        Alle offenen Positionen unter 50% ihres Hold-Horizons. Kein Decay.
      </div>
    );
  }

  return (
    <ul className="space-y-2 text-sm">
      {flags.map((f) => {
        const tone =
          f.severity === "warn"
            ? "border-amber-700 bg-amber-900/20 text-amber-200"
            : "border-zinc-700 bg-zinc-800/30 text-zinc-200";
        const pct = (f.held_days / f.hold_max) * 100;
        const pnlTone = f.pnl_pct > 0 ? "text-emerald-400" : f.pnl_pct < 0 ? "text-rose-400" : "text-zinc-400";
        return (
          <li
            key={f.ticker}
            className={`px-3 py-2 rounded border ${tone}`}
          >
            <div className="flex items-baseline gap-2">
              <span className="font-semibold">{f.ticker}</span>
              <span className="text-xs uppercase tracking-wider opacity-70">
                {f.severity}
              </span>
              <span className={`ml-auto text-sm font-mono ${pnlTone}`}>
                {f.pnl_pct >= 0 ? "+" : ""}
                {f.pnl_pct.toFixed(2)}%
              </span>
            </div>
            <div className="mt-1 text-xs opacity-80">
              Held {f.held_days}d / max {f.hold_max}d ({pct.toFixed(0)}%)
            </div>
            <div className="mt-1.5 h-1 rounded bg-zinc-800 overflow-hidden">
              <div
                className={`h-full ${
                  f.severity === "warn" ? "bg-amber-500" : "bg-zinc-500"
                }`}
                style={{ width: `${Math.min(100, pct)}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
