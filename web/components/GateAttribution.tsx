"use client";

import { useEffect, useMemo, useState } from "react";
import type { GateAttribution, GateBlock } from "@/lib/types";
import { aggregateGateBlocks } from "@/lib/compute";

export default function GateAttributionCard() {
  const [blocks, setBlocks] = useState<GateBlock[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/gate-blocks", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as { blocks: GateBlock[] };
        setBlocks(data.blocks);
      } catch (e) {
        setErr((e as Error).message);
      }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  const summary: GateAttribution[] = useMemo(
    () => (blocks ? aggregateGateBlocks(blocks) : []),
    [blocks],
  );
  const lastN = useMemo(() => (blocks ? blocks.slice(-12).reverse() : []), [blocks]);

  if (err) {
    return <div className="text-sm text-rose-400">Load failed: {err}</div>;
  }
  if (!blocks) {
    return <div className="text-sm text-zinc-500">Lade…</div>;
  }
  if (blocks.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        Keine Gate-Events. JSONL leert nach 5MB Rotation, oder noch keine Recommendation gegated.
      </div>
    );
  }

  const maxBlocks = Math.max(1, ...summary.map((s) => s.blocks));

  return (
    <div className="space-y-4">
      <div>
        <div className="text-xs uppercase tracking-wider text-zinc-500 mb-2">
          Block-Counts ({blocks.length} Events)
        </div>
        <ul className="space-y-1.5">
          {summary.map((s) => (
            <li key={s.gate} className="flex items-center gap-2 text-sm">
              <span className="w-32 truncate text-zinc-300">{s.gate}</span>
              <div className="flex-1 h-2 bg-zinc-800 rounded overflow-hidden">
                <div
                  className={`h-full ${s.gate === "all_passed" ? "bg-emerald-500" : "bg-rose-500"}`}
                  style={{ width: `${(s.blocks / maxBlocks) * 100}%` }}
                />
              </div>
              <span className="w-20 text-right text-xs text-zinc-400 tabular-nums">
                {s.blocks}b / {s.passes}p
              </span>
              <span className="w-12 text-right text-xs text-zinc-500 tabular-nums">
                {s.block_rate}%
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <div className="text-xs uppercase tracking-wider text-zinc-500 mb-2">
          Letzte Events
        </div>
        <ul className="space-y-1 text-xs font-mono">
          {lastN.map((b, i) => (
            <li key={i} className="flex gap-2 text-zinc-400">
              <span className="text-zinc-500">{b.ts.slice(5)}</span>
              <span
                className={
                  b.blocked ? "text-rose-400 w-4" : "text-emerald-400 w-4"
                }
              >
                {b.blocked ? "✗" : "✓"}
              </span>
              <span className="w-20 truncate text-zinc-300">{b.ticker}</span>
              <span className="w-28 truncate text-zinc-400">{b.gate}</span>
              <span className="flex-1 truncate text-zinc-500">{b.reason}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
