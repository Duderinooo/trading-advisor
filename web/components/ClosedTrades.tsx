import type { CashMovement, ClosedTrade } from "@/lib/types";

// Sum dividends linked to a trade (mirrors core.portfolio.trade_dividends).
function tradeDivsEur(t: ClosedTrade, movements: CashMovement[]): number {
  if (!movements.length) return 0;
  let sum = 0;
  for (const m of movements) {
    if (m.kind !== "dividend") continue;
    const link = m.linked_trade;
    if (!link) continue;
    if (link.ticker.toUpperCase() !== t.ticker.toUpperCase()) continue;
    if (link.entry_date !== t.entry_date) continue;
    if (link.exit_date !== t.exit_date) continue;
    sum += Number(m.amount ?? 0);
  }
  return sum;
}

export default function ClosedTrades({
  trades,
  movements = [],
  limit = 25,
}: {
  trades: ClosedTrade[];
  movements?: CashMovement[];
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
            const divs = tradeDivsEur(t, movements);
            const basePnlEur = Number(t.pnl_eur ?? 0);
            const effPnlEur = basePnlEur + divs;
            // Effective % uses size-from-entry-and-shares since size_eur isn't on
            // ClosedTrade type.
            const sizeEur = Number(t.entry_price ?? 0) * Number(t.shares ?? 0);
            const basePnlPct = Number(t.pnl_pct ?? 0);
            const effPnlPct =
              divs && sizeEur > 0 ? basePnlPct + (divs / sizeEur) * 100 : basePnlPct;
            const win = effPnlPct > 0;
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
                  {effPnlPct.toFixed(2)}%
                  {divs > 0 && (
                    <span className="ml-1 text-[10px] text-emerald-300">
                      (+€{divs.toFixed(2)} div)
                    </span>
                  )}
                </td>
                <td
                  className={`py-2 pr-3 ${win ? "text-emerald-400" : "text-rose-400"}`}
                  title={
                    divs > 0
                      ? `Price ${basePnlEur.toFixed(2)} + Div ${divs.toFixed(2)} = ${effPnlEur.toFixed(2)}`
                      : undefined
                  }
                >
                  {effPnlEur.toFixed(2)}
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
