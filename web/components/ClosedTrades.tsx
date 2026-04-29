import type { ClosedTrade } from "@/lib/types";

export default function ClosedTrades({
  trades,
  limit = 25,
}: {
  trades: ClosedTrade[];
  limit?: number;
}) {
  if (trades.length === 0) {
    return <div className="text-sm text-zinc-500">Noch keine geschlossenen Trades.</div>;
  }
  const recent = [...trades]
    .sort((a, b) => (b.exit_date ?? "").localeCompare(a.exit_date ?? ""))
    .slice(0, limit);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
            <th className="py-2 pr-3">Datum</th>
            <th className="py-2 pr-3">Ticker</th>
            <th className="py-2 pr-3">Entry</th>
            <th className="py-2 pr-3">Exit</th>
            <th className="py-2 pr-3">PnL %</th>
            <th className="py-2 pr-3">PnL €</th>
            <th className="py-2 pr-3">Reason</th>
            <th className="py-2 pr-3">Mistake</th>
          </tr>
        </thead>
        <tbody>
          {recent.map((t, i) => {
            const win = (t.pnl_pct ?? 0) > 0;
            return (
              <tr
                key={`${t.ticker}-${t.exit_date}-${i}`}
                className="border-b border-zinc-900/60 hover:bg-zinc-900/30"
              >
                <td className="py-2 pr-3 text-zinc-500">
                  {t.exit_date?.split(" ")[0]}
                </td>
                <td className="py-2 pr-3 font-mono">
                  {t.ticker}
                  {t.partial && (
                    <span className="ml-1 text-xs text-amber-400">P{t.partial_seq ?? ""}</span>
                  )}
                </td>
                <td className="py-2 pr-3">{t.entry_price.toFixed(2)}</td>
                <td className="py-2 pr-3">{t.exit_price?.toFixed(2)}</td>
                <td
                  className={`py-2 pr-3 font-medium ${win ? "text-emerald-400" : "text-rose-400"}`}
                >
                  {(t.pnl_pct ?? 0).toFixed(2)}%
                </td>
                <td
                  className={`py-2 pr-3 ${win ? "text-emerald-400" : "text-rose-400"}`}
                >
                  {(t.pnl_eur ?? 0).toFixed(2)}
                </td>
                <td className="py-2 pr-3 text-zinc-400 max-w-xs truncate">
                  {t.exit_reason ?? "—"}
                </td>
                <td className="py-2 pr-3 text-zinc-500 text-xs">
                  {t.mistake_class ?? (win ? "—" : "untagged")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
