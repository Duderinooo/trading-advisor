import type { LiveQuote, WatchLevel } from "@/lib/types";

type Props = {
  levels: WatchLevel[];
  liveQuotes?: Record<string, LiveQuote>;
};

function fmtPrice(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toFixed(2);
}

function distancePct(live: number | null | undefined, trigger: number): string | null {
  if (live == null || !trigger) return null;
  const d = ((live - trigger) / trigger) * 100;
  return `${d >= 0 ? "+" : ""}${d.toFixed(2)}%`;
}

// Compound-condition gates that must ALL pass for a watch to fire. Surfaced so
// user can debug why a watch isn't triggering at a glance.
function ConditionPills({ w, live }: { w: WatchLevel; live: number | null }) {
  const pills: { label: string; value: string; tone: string }[] = [];
  if (typeof w.confirm_close_above === "number") {
    const ok = live != null && live >= w.confirm_close_above;
    pills.push({
      label: "confirm≥",
      value: w.confirm_close_above.toFixed(2),
      tone: live == null
        ? "bg-zinc-800 text-zinc-400"
        : ok
          ? "bg-emerald-900/40 text-emerald-300"
          : "bg-rose-900/40 text-rose-300",
    });
  }
  if (typeof w.invalidate_below === "number") {
    const danger = live != null && live < w.invalidate_below;
    pills.push({
      label: "invalid<",
      value: w.invalidate_below.toFixed(2),
      tone: live == null
        ? "bg-zinc-800 text-zinc-400"
        : danger
          ? "bg-rose-900/60 text-rose-200"
          : "bg-zinc-800 text-zinc-400",
    });
  }
  if (typeof w.min_volume_ratio === "number") {
    pills.push({
      label: "vol≥",
      value: w.min_volume_ratio.toFixed(2),
      tone: "bg-zinc-800 text-zinc-400",
    });
  }
  if (w.valid_until) {
    pills.push({
      label: "exp",
      value: w.valid_until,
      tone: "bg-zinc-800 text-zinc-500",
    });
  }
  if (pills.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {pills.map((p, idx) => (
        <span
          key={idx}
          className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${p.tone}`}
        >
          {p.label}
          {p.value}
        </span>
      ))}
    </span>
  );
}

export default function WatchLevels({ levels, liveQuotes }: Props) {
  if (levels.length === 0) {
    return <div className="text-sm text-zinc-500">Keine aktiven Watch-Level.</div>;
  }
  return (
    <ul className="divide-y divide-zinc-900/60">
      {levels.map((w, i) => {
        const lq = liveQuotes?.[w.ticker];
        const live = lq?.price ?? null;
        const dist = distancePct(live, w.trigger_price);
        const distNeg = dist?.startsWith("-");
        const thesis = w.thesis || w.note;
        return (
          <li
            key={`${w.ticker}-${w.type}-${i}`}
            className="py-3 first:pt-0 last:pb-0 space-y-1.5"
          >
            {/* Row 1: header */}
            <div className="flex items-center gap-3 flex-wrap text-sm">
              <span className="font-mono text-zinc-100 font-medium">{w.ticker}</span>
              <span className="text-[11px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-300">
                {w.type}
              </span>
              <span className="text-zinc-400 text-xs">trigger</span>
              <span className="text-zinc-200 tabular-nums">€{w.trigger_price}</span>
              {live != null && (
                <>
                  <span className="text-zinc-700">·</span>
                  <span className="text-zinc-400 text-xs">live</span>
                  <span className="font-mono text-zinc-200 tabular-nums">
                    €{fmtPrice(live)}
                  </span>
                  {dist && (
                    <span
                      className={`tabular-nums text-xs ${distNeg ? "text-rose-400" : "text-emerald-400"}`}
                    >
                      ({dist})
                    </span>
                  )}
                  {lq?.ts && (
                    <span className="ml-auto text-[11px] text-zinc-600 tabular-nums">
                      {lq.ts}
                    </span>
                  )}
                </>
              )}
            </div>

            {/* Row 2: condition pills (full width, wraps if needed) */}
            <div className="w-full">
              <ConditionPills w={w} live={live} />
            </div>

            {/* Row 3: thesis / note (full width) */}
            {thesis && (
              <div className="w-full text-xs text-zinc-500 leading-relaxed">
                {thesis}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
