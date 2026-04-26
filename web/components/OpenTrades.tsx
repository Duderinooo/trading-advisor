import type { OpenTrade } from "@/lib/types";

function tpFmt(tp: OpenTrade["take_profit"]) {
  if (tp == null) return "—";
  if (Array.isArray(tp)) return tp.map((x) => x.toFixed(2)).join(" / ");
  return tp.toFixed(2);
}

export default function OpenTrades({ trades }: { trades: OpenTrade[] }) {
  if (trades.length === 0) {
    return <div className="text-sm text-zinc-500">Keine offenen Positionen.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
            <th className="py-2 pr-3">Ticker</th>
            <th className="py-2 pr-3">Entry</th>
            <th className="py-2 pr-3">Shares</th>
            <th className="py-2 pr-3">Size €</th>
            <th className="py-2 pr-3">SL</th>
            <th className="py-2 pr-3">TP</th>
            <th className="py-2 pr-3">Conv</th>
            <th className="py-2 pr-3">Datum</th>
            <th className="py-2 pr-3">Thesis</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const size = t.size_eur ?? t.entry_price * t.shares;
            const rt = t.red_team_review;
            const rtTone =
              rt?.verdict === "WEAKEN"
                ? "bg-amber-900/40 text-amber-300 border-amber-800"
                : rt?.verdict === "APPROVE"
                  ? "bg-emerald-900/30 text-emerald-300 border-emerald-800"
                  : "";
            return (
              <tr
                key={`${t.ticker}-${i}`}
                className="border-b border-zinc-900/60 hover:bg-zinc-900/30"
              >
                <td className="py-2 pr-3 font-mono">
                  {t.ticker}
                  {rt && (
                    <span
                      title={`Red-Team: ${rt.verdict} (conf ${rt.confidence_thesis_holds})\n• ${rt.top_failure_modes.join("\n• ")}`}
                      className={`ml-1 px-1.5 py-0.5 text-[10px] rounded border ${rtTone}`}
                    >
                      🐻 {rt.verdict[0]}
                    </span>
                  )}
                </td>
                <td className="py-2 pr-3">{t.entry_price.toFixed(2)}</td>
                <td className="py-2 pr-3">{t.shares}</td>
                <td className="py-2 pr-3">{size.toFixed(2)}</td>
                <td className="py-2 pr-3 text-rose-400">
                  {t.stop_loss.toFixed(2)}
                </td>
                <td className="py-2 pr-3 text-emerald-400">
                  {tpFmt(t.take_profit)}
                </td>
                <td className="py-2 pr-3">{t.conviction ?? "—"}</td>
                <td className="py-2 pr-3 text-zinc-500">
                  {t.entry_date.split(" ")[0]}
                </td>
                <td className="py-2 pr-3 text-zinc-400 max-w-md truncate">
                  {t.thesis ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
