import type { SetupTypeStats } from "@/lib/types";

export default function SetupTypeBreakdown({
  stats,
}: {
  stats: SetupTypeStats[];
}) {
  if (stats.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        Noch keine geschlossenen Trades mit setup_type.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
            <th className="py-2 pr-3">Setup-Type</th>
            <th className="py-2 pr-3 text-right">N</th>
            <th className="py-2 pr-3 text-right">Win-Rate</th>
            <th className="py-2 pr-3 text-right">Avg P&amp;L %</th>
            <th className="py-2 pr-3 text-right">Total P&amp;L €</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((s) => {
            const pnlTone =
              s.total_pnl_eur > 0
                ? "text-emerald-400"
                : s.total_pnl_eur < 0
                  ? "text-rose-400"
                  : "text-zinc-300";
            const wrTone =
              s.total < 5
                ? "text-zinc-500"
                : s.win_rate >= 60
                  ? "text-emerald-400"
                  : s.win_rate >= 45
                    ? "text-amber-400"
                    : "text-rose-400";
            return (
              <tr
                key={s.setup_type}
                className="border-b border-zinc-900/60 hover:bg-zinc-900/30"
              >
                <td className="py-2 pr-3 font-mono text-zinc-200">
                  {s.setup_type}
                </td>
                <td className="py-2 pr-3 text-right text-zinc-300">
                  {s.total}
                </td>
                <td className={`py-2 pr-3 text-right ${wrTone}`}>
                  {s.win_rate.toFixed(1)}%
                  <span className="text-zinc-600 text-xs">
                    {" "}
                    ({s.wins}/{s.total})
                  </span>
                </td>
                <td className={`py-2 pr-3 text-right ${pnlTone}`}>
                  {s.avg_pnl_pct >= 0 ? "+" : ""}
                  {s.avg_pnl_pct.toFixed(2)}%
                </td>
                <td className={`py-2 pr-3 text-right ${pnlTone}`}>
                  {s.total_pnl_eur >= 0 ? "+" : ""}
                  {s.total_pnl_eur.toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="mt-2 text-xs text-zinc-600">
        Win-Rate-Färbung: ≥60% grün, ≥45% gelb, &lt;45% rot. Grau bei n&lt;5
        (statistisch nicht aussagekräftig).
      </div>
    </div>
  );
}
