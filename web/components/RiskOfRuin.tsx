import type { RuinStats } from "@/lib/compute";

type Props = {
  ruin: RuinStats | null;
  lossStreak: { current: number; max: number };
};

export default function RiskOfRuin({ ruin, lossStreak }: Props) {
  if (!ruin) {
    return (
      <div className="text-sm text-zinc-500">
        ≥10 geschlossene Trades nötig für Monte-Carlo-Simulation.
      </div>
    );
  }
  const tone =
    ruin.ruin_prob_pct < 5
      ? "text-emerald-400"
      : ruin.ruin_prob_pct < 15
        ? "text-amber-400"
        : "text-rose-400";
  return (
    <div className="space-y-3 text-sm">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            P(Ruin {ruin.ruin_threshold_pct}%)
          </div>
          <div className={`text-lg font-semibold ${tone}`}>
            {ruin.ruin_prob_pct}%
          </div>
          <div className="text-xs text-zinc-500">
            in {ruin.horizon} Trades · {ruin.n_trials} Trials
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Median Terminal
          </div>
          <div
            className={
              ruin.median_terminal_return_pct >= 0
                ? "text-emerald-400 text-lg font-semibold"
                : "text-rose-400 text-lg font-semibold"
            }
          >
            {ruin.median_terminal_return_pct >= 0 ? "+" : ""}
            {ruin.median_terminal_return_pct}%
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Worst 5%
          </div>
          <div className="text-rose-400 text-lg font-semibold">
            {ruin.worst_5pct_return_pct >= 0 ? "+" : ""}
            {ruin.worst_5pct_return_pct}%
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Loss Streak
          </div>
          <div className="text-zinc-100 text-lg font-semibold">
            now {lossStreak.current} · max {lossStreak.max}
          </div>
        </div>
      </div>
      <p className="text-xs text-zinc-600">
        Bootstrap-resample der bisherigen pnl_pct-Verteilung, je Trade 5%
        Equity-Risk. Worst-5% = 5er-Perzentil der Endkapital-Verteilung.
      </p>
    </div>
  );
}
