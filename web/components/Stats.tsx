import type { HitStats } from "@/lib/types";

export default function Stats({ stats }: { stats: HitStats | null }) {
  if (!stats) {
    return <div className="text-sm text-zinc-500">≥3 geschlossene Trades nötig.</div>;
  }
  const convs = Object.entries(stats.conv_breakdown).sort(
    (a, b) => Number(a[0]) - Number(b[0]),
  );
  const mistakes = Object.entries(stats.mistake_classes).sort(
    (a, b) => b[1] - a[1],
  );
  return (
    <div className="space-y-4 text-sm">
      <div className="grid grid-cols-2 gap-3">
        <Pair label="Trades" value={`${stats.total}`} />
        <Pair label="Win-Rate" value={`${stats.win_rate}%`} />
        <Pair
          label="Avg Win"
          value={`${stats.avg_win_pct.toFixed(2)}%`}
          tone="good"
        />
        <Pair
          label="Avg Loss"
          value={`${stats.avg_loss_pct.toFixed(2)}%`}
          tone="bad"
        />
        <Pair
          label="R-Multiple"
          value={stats.r_multiple !== null ? stats.r_multiple.toFixed(2) : "—"}
        />
        <Pair label="Streak (5)" value={stats.streak || "—"} />
      </div>

      {convs.length > 0 && (
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">
            Per Conviction
          </div>
          <ul className="space-y-1">
            {convs.map(([c, s]) => (
              <li key={c} className="flex justify-between">
                <span>Conv {c}</span>
                <span className="text-zinc-400">
                  {s.wins}/{s.total} ({s.rate}%)
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {stats.calibration && (
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">
            Calibration (n={stats.calibration.n})
          </div>
          <ul className="space-y-1">
            <li className="flex justify-between">
              <span>Brier</span>
              <span className="text-zinc-400">
                {stats.calibration.avg_brier.toFixed(4)}
              </span>
            </li>
            <li className="flex justify-between">
              <span>Predicted</span>
              <span className="text-zinc-400">
                {(stats.calibration.avg_p_predicted * 100).toFixed(1)}%
              </span>
            </li>
            <li className="flex justify-between">
              <span>Actual</span>
              <span className="text-zinc-400">
                {(stats.calibration.actual_win_rate * 100).toFixed(1)}%
              </span>
            </li>
            <li className="flex justify-between">
              <span>Bias</span>
              <span
                className={
                  stats.calibration.bias > 0.05
                    ? "text-rose-400"
                    : stats.calibration.bias < -0.05
                      ? "text-amber-400"
                      : "text-zinc-400"
                }
              >
                {(stats.calibration.bias * 100).toFixed(1)}%
              </span>
            </li>
            {stats.calibration.haircut !== 0 && (
              <li className="flex justify-between">
                <span>Haircut aktiv</span>
                <span className="text-amber-400">
                  {(stats.calibration.haircut * 100).toFixed(1)}%
                </span>
              </li>
            )}
          </ul>
        </div>
      )}

      {mistakes.length > 0 && (
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">
            Mistakes (last 20 losses)
          </div>
          <ul className="space-y-1">
            {mistakes.map(([cls, n]) => (
              <li key={cls} className="flex justify-between">
                <span
                  className={
                    cls === "untagged" ? "text-zinc-500 italic" : "text-zinc-300"
                  }
                >
                  {cls}
                </span>
                <span className="text-zinc-400">{n}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Pair({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  const cls =
    tone === "good"
      ? "text-emerald-400"
      : tone === "bad"
        ? "text-rose-400"
        : "text-zinc-100";
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      <div className={`text-lg font-semibold ${cls}`}>{value}</div>
    </div>
  );
}
