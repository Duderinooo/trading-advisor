"use client";

import { useMemo, useState } from "react";
import type { OpenTrade } from "@/lib/types";
import { simulateShock } from "@/lib/compute";

const PRESETS = [-1, -2, -3, -5, -8];

export default function WhatIfShock({ open }: { open: OpenTrade[] }) {
  const [shock, setShock] = useState(-3);

  const sim = useMemo(() => simulateShock(open, shock), [open, shock]);

  if (open.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        Keine offenen Positionen — kein Shock zu simulieren.
      </div>
    );
  }

  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs uppercase tracking-wider text-zinc-500">
          Shock %
        </span>
        {PRESETS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setShock(p)}
            className={`px-2 py-1 text-xs rounded border ${
              shock === p
                ? "bg-zinc-100 text-zinc-900 border-zinc-100"
                : "bg-zinc-900 text-zinc-300 border-zinc-800 hover:border-zinc-700"
            }`}
          >
            {p}%
          </button>
        ))}
        <input
          type="number"
          step={0.5}
          value={shock}
          onChange={(e) => setShock(Number(e.target.value))}
          className="w-20 px-2 py-1 text-xs rounded bg-zinc-900 border border-zinc-800 text-zinc-200"
        />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Total P&L
          </div>
          <div
            className={`text-lg font-semibold ${sim.total_loss_eur < 0 ? "text-rose-400" : "text-emerald-400"}`}
          >
            {sim.total_loss_eur >= 0 ? "+" : ""}
            {sim.total_loss_eur.toFixed(2)} €
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            SL Hits
          </div>
          <div
            className={`text-lg font-semibold ${sim.sl_hits > 0 ? "text-rose-400" : "text-zinc-100"}`}
          >
            {sim.sl_hits} / {open.length}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Cascade
          </div>
          <div className="text-lg font-semibold text-zinc-300">
            {sim.sl_hits === open.length && open.length > 0
              ? "ALLE STOPS"
              : sim.sl_hits === 0
                ? "—"
                : `${sim.sl_hits} Position(en)`}
          </div>
        </div>
      </div>

      <ul className="space-y-1 text-xs font-mono">
        {sim.results.map((r) => (
          <li key={r.ticker} className="flex gap-2">
            <span className="w-20 truncate text-zinc-300">{r.ticker}</span>
            <span className="w-24 text-right text-zinc-400 tabular-nums">
              €{r.shocked_price.toFixed(2)}
            </span>
            <span
              className={`w-20 text-right tabular-nums ${
                r.hits_sl ? "text-rose-400 font-semibold" : "text-zinc-400"
              }`}
            >
              {r.hits_sl ? "SL HIT" : `${r.loss_pct.toFixed(1)}%`}
            </span>
            <span
              className={`flex-1 text-right tabular-nums ${
                r.loss_eur < 0 ? "text-rose-400" : "text-emerald-400"
              }`}
            >
              {r.loss_eur >= 0 ? "+" : ""}
              {r.loss_eur.toFixed(2)} €
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
