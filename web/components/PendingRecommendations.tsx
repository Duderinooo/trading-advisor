"use client";

import { useEffect, useState } from "react";
import type { PendingRecommendation } from "@/lib/types";

const TTL_HOURS = 4;

const KIND_META: Record<
  string,
  { label: string; color: string; emoji: string }
> = {
  entry: {
    label: "ENTRY",
    color: "bg-emerald-900/40 text-emerald-300 border-emerald-800",
    emoji: "🎯",
  },
  add: {
    label: "ADD",
    color: "bg-cyan-900/40 text-cyan-300 border-cyan-800",
    emoji: "🎯",
  },
  update: {
    label: "UPDATE",
    color: "bg-amber-900/40 text-amber-300 border-amber-800",
    emoji: "🔧",
  },
  exit: {
    label: "EXIT",
    color: "bg-rose-900/40 text-rose-300 border-rose-800",
    emoji: "🎯",
  },
};

function ageMinutes(ts: string | undefined, now: Date): number | null {
  if (!ts) return null;
  // ts format from bot: "YYYY-MM-DD HH:MM"
  const m = ts.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/);
  if (!m) return null;
  const dt = new Date(
    Number(m[1]),
    Number(m[2]) - 1,
    Number(m[3]),
    Number(m[4]),
    Number(m[5]),
  );
  return Math.max(0, Math.round((now.getTime() - dt.getTime()) / 60000));
}

function fmtAge(min: number): string {
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${h}h${m.toString().padStart(2, "0")}m`;
}

function summarize(rec: PendingRecommendation): string {
  const kind = (rec.kind ?? "entry") as keyof typeof KIND_META;
  if (kind === "entry") {
    const tp = Array.isArray(rec.take_profit)
      ? rec.take_profit.map((t) => `€${t.toFixed(2)}`).join("/")
      : rec.take_profit
        ? `€${rec.take_profit.toFixed(2)}`
        : "—";
    return `Entry €${rec.entry_price?.toFixed(2) ?? "?"} | SL €${rec.stop_loss?.toFixed(2) ?? "?"} | TP ${tp} | €${rec.size_eur?.toFixed(0) ?? "?"} | Conv ${rec.conviction ?? "?"}/5`;
  }
  if (kind === "add") {
    return `+€${rec.additional_size_eur?.toFixed(0) ?? "?"} | Conv ${rec.conviction ?? "?"}/5 | ${rec.trigger ?? rec.thesis_reinforcement ?? "—"}`;
  }
  if (kind === "update") {
    const sl =
      rec.new_stop_loss != null ? `SL→€${rec.new_stop_loss.toFixed(2)}` : "";
    const tp = rec.new_take_profit
      ? `TP→${
          Array.isArray(rec.new_take_profit)
            ? rec.new_take_profit.map((t) => `€${t.toFixed(2)}`).join("/")
            : `€${rec.new_take_profit.toFixed(2)}`
        }`
      : "";
    return `${[sl, tp].filter(Boolean).join(" | ")} | ${rec.reason ?? "—"}`;
  }
  if (kind === "exit") {
    return `${rec.urgency ?? "today"} | ${rec.reason ?? "—"}`;
  }
  return "—";
}

export default function PendingRecommendations({
  recs,
}: {
  recs: PendingRecommendation[] | undefined;
}) {
  // Avoid hydration mismatch: now() is client-only.
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(id);
  }, []);

  if (!recs || recs.length === 0) {
    return (
      <div className="text-sm text-zinc-500">Keine pending Empfehlungen.</div>
    );
  }

  return (
    <ul className="space-y-2">
      {recs.map((rec, i) => {
        const kind = (rec.kind ?? "entry") as keyof typeof KIND_META;
        const meta = KIND_META[kind] ?? KIND_META.entry;
        const age = now ? ageMinutes(rec.timestamp, now) : null;
        const expired = age != null && age > TTL_HOURS * 60;
        const ttlTone = expired
          ? "text-rose-400"
          : age != null && age > 180
            ? "text-amber-400"
            : "text-zinc-500";
        return (
          <li
            key={`${rec.ticker}-${rec.timestamp ?? i}-${i}`}
            className="flex flex-col gap-1 border border-zinc-800 rounded p-3 bg-zinc-900/30"
          >
            <div className="flex items-center gap-2 flex-wrap text-sm">
              <span
                className={`text-xs px-2 py-0.5 rounded border ${meta.color}`}
              >
                {meta.emoji} {meta.label}
              </span>
              <span className="font-mono text-zinc-100">{rec.ticker}</span>
              <span className={`text-xs ml-auto ${ttlTone}`}>
                {age != null ? `Age ${fmtAge(age)}` : "Age ?"}
                {expired ? " (TTL abgelaufen)" : ""}
              </span>
            </div>
            <div className="text-xs text-zinc-300">{summarize(rec)}</div>
            {rec.thesis && (
              <div className="text-xs text-zinc-500 italic">{rec.thesis}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
