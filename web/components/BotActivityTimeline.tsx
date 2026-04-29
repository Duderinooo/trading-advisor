"use client";

import { useEffect, useState } from "react";
import type { GateBlock } from "@/lib/types";

const REFRESH_MS = 30_000;

const PASS_GATES = new Set(["all_passed", "add_all_passed", "update_all_passed", "exit_all_passed"]);

function gateLabel(g: string, blocked: boolean): string {
  if (g === "all_passed") return "ENTRY approved";
  if (g === "add_all_passed") return "ADD approved";
  if (g === "update_all_passed") return "SL/TP UPDATE approved";
  if (g === "exit_all_passed") return "EXIT approved";
  if (g === "already_open") return "Re-Entry suppressed (already open)";
  return blocked ? `BLOCKED: ${g}` : g;
}

function tone(blocked: boolean, gate: string) {
  if (PASS_GATES.has(gate)) return "border-emerald-800 bg-emerald-900/20";
  if (blocked) return "border-rose-900 bg-rose-900/20";
  return "border-zinc-800 bg-zinc-900/30";
}

function fmtTs(ts: string): string {
  // Expect "YYYY-MM-DDTHH:MM:SS" or similar
  const m = ts.match(/(\d{2}):(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : ts.slice(-8, -3);
}

function fmtDate(ts: string): string {
  const m = ts.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : ts.slice(0, 10);
}

export default function BotActivityTimeline() {
  const [blocks, setBlocks] = useState<GateBlock[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/gate-blocks?limit=200", {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as { blocks: GateBlock[] };
        if (!cancelled) setBlocks(data.blocks ?? []);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (loading) {
    return <div className="text-sm text-zinc-500">Lade…</div>;
  }
  if (blocks.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        Keine Bot-Aktivität in gate_blocks.jsonl.
      </div>
    );
  }

  // Reverse chrono (newest first), group by date
  const reversed = [...blocks].reverse();
  const visible = showAll ? reversed : reversed.slice(0, 30);

  // Group by date for visual scan
  const groups: Record<string, GateBlock[]> = {};
  for (const b of visible) {
    const d = fmtDate(b.ts);
    (groups[d] ??= []).push(b);
  }
  const dates = Object.keys(groups).sort().reverse();

  return (
    <div className="space-y-3">
      {dates.map((date) => (
        <div key={date}>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">
            {date}
          </div>
          <ul className="space-y-1">
            {groups[date].map((b, i) => (
              <li
                key={`${b.ts}-${b.ticker}-${i}`}
                className={`flex items-start gap-3 text-xs px-3 py-1.5 rounded border ${tone(
                  b.blocked,
                  b.gate,
                )}`}
              >
                <span className="text-zinc-500 font-mono w-12 shrink-0">
                  {fmtTs(b.ts)}
                </span>
                <span className="font-mono text-zinc-100 w-20 shrink-0">
                  {b.ticker}
                </span>
                <span className="text-zinc-300 shrink-0">
                  {gateLabel(b.gate, b.blocked)}
                </span>
                <span className="text-zinc-500 truncate">{b.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {!showAll && reversed.length > 30 && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="text-xs text-zinc-500 hover:text-zinc-200"
        >
          {reversed.length - 30} weitere zeigen
        </button>
      )}
    </div>
  );
}
