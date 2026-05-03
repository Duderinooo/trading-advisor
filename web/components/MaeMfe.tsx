import type { MaeMfeStats } from "@/lib/compute";

export default function MaeMfe({ stats }: { stats: MaeMfeStats | null }) {
  if (!stats || stats.rows.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        Noch keine MAE/MFE-Daten. Heartbeat akkumuliert beim nächsten
        offenen Trade automatisch (mae/mfe-Felder pro open_trade).
      </div>
    );
  }
  const fmt = (v: number | null) =>
    v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}R`;
  return (
    <div className="space-y-3 text-sm">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat
          label="Win avg MAE"
          value={fmt(stats.win_avg_mae_r)}
          tone="good"
          hint="Wie tief ins Rote sind Winner gegangen vor Reversal?"
        />
        <Stat
          label="Loss avg MAE"
          value={fmt(stats.loss_avg_mae_r)}
          tone="bad"
          hint="Bei welchem R-Multiple stoppt SL aktuell raus?"
        />
        <Stat
          label="Win avg MFE"
          value={fmt(stats.win_avg_mfe_r)}
          tone="good"
          hint="Wie weit lief Winner maximal vorm Exit?"
        />
        <Stat
          label="Loss avg MFE"
          value={fmt(stats.loss_avg_mfe_r)}
          tone="bad"
          hint="Hatten Losses kurze Pop-Phasen ungenutzt?"
        />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-zinc-500 text-left">
              <th className="py-1 pr-2">Trade</th>
              <th className="py-1 pr-2">Exit</th>
              <th className="py-1 pr-2 text-right">PnL%</th>
              <th className="py-1 pr-2 text-right">MAE</th>
              <th className="py-1 pr-2 text-right">MFE</th>
            </tr>
          </thead>
          <tbody>
            {stats.rows.slice(0, 12).map((r, i) => (
              <tr key={`${r.ticker}-${r.exit_date}-${i}`} className="border-t border-zinc-900">
                <td className="py-1 pr-2 font-mono text-zinc-200">{r.ticker}</td>
                <td className="py-1 pr-2 text-zinc-500">
                  {r.exit_date.split(" ")[0]}
                </td>
                <td
                  className={`py-1 pr-2 text-right ${
                    r.pnl_pct >= 0 ? "text-emerald-400" : "text-rose-400"
                  }`}
                >
                  {r.pnl_pct >= 0 ? "+" : ""}
                  {r.pnl_pct.toFixed(2)}
                </td>
                <td className="py-1 pr-2 text-right text-zinc-300 font-mono">
                  {fmt(r.mae_r)}
                </td>
                <td className="py-1 pr-2 text-right text-zinc-300 font-mono">
                  {fmt(r.mfe_r)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-zinc-600">
        R = Entry − SL Distance. MAE/MFE als R-Multiple — direkter Vergleich
        SL-Wahl vs realer Excursion. Heartbeat tickt min/max alle ~10s.
      </p>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
  hint?: string;
}) {
  const cls =
    tone === "good"
      ? "text-emerald-400"
      : tone === "bad"
        ? "text-rose-400"
        : "text-zinc-100";
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-zinc-500">{label}</div>
      <div className={`text-lg font-semibold ${cls}`}>{value}</div>
      {hint && <div className="text-xs text-zinc-600 mt-1">{hint}</div>}
    </div>
  );
}
