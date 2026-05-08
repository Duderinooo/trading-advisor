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
    <ul className="space-y-2">
      {levels.map((w, i) => {
        const lq = liveQuotes?.[w.ticker];
        const live = lq?.price ?? null;
        const dist = distancePct(live, w.trigger_price);
        return (
          <li
            key={`${w.ticker}-${w.type}-${i}`}
            className="flex items-start gap-3 text-sm flex-wrap"
          >
            <span className="font-mono text-zinc-100 min-w-20">{w.ticker}</span>
            <span className="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-300">
              {w.type}
            </span>
            <span className="text-zinc-300">@ {w.trigger_price}</span>
            {live != null && (
              <span className="text-xs font-mono text-zinc-300">
                live {fmtPrice(live)}
                {dist && (
                  <span
                    className={
                      dist.startsWith("-")
                        ? "ml-1 text-rose-400"
                        : "ml-1 text-emerald-400"
                    }
                  >
                    ({dist})
                  </span>
                )}
                {lq?.ts && <span className="ml-1 text-zinc-600">{lq.ts}</span>}
              </span>
            )}
            <ConditionPills w={w} live={live} />
            {w.note && (
              <span className="text-zinc-500 text-xs flex-1">{w.note}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
