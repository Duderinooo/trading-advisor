import type { WatchLevel } from "@/lib/types";

export default function WatchLevels({ levels }: { levels: WatchLevel[] }) {
  if (levels.length === 0) {
    return <div className="text-sm text-zinc-500">Keine aktiven Watch-Level.</div>;
  }
  return (
    <ul className="space-y-2">
      {levels.map((w, i) => (
        <li
          key={`${w.ticker}-${w.type}-${i}`}
          className="flex items-start gap-3 text-sm"
        >
          <span className="font-mono text-zinc-100 min-w-20">{w.ticker}</span>
          <span className="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-300">
            {w.type}
          </span>
          <span className="text-zinc-300">@ {w.trigger_price}</span>
          {w.note && (
            <span className="text-zinc-500 text-xs flex-1">{w.note}</span>
          )}
        </li>
      ))}
    </ul>
  );
}
