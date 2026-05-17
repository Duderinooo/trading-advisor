import { readPortfolio, readGateBlocks } from "@/lib/portfolio";
import {
  computeHitStats,
  preMortemAccuracy,
  summarizeGateActivityDay,
  recentGateDays,
  MIN_CALIBRATION_N,
} from "@/lib/compute";

export const dynamic = "force-dynamic";

const MISTAKE_COLORS: Record<string, string> = {
  prediction: "text-rose-400",
  timing: "text-amber-400",
  execution: "text-sky-400",
  external: "text-violet-400",
  untagged: "text-zinc-500",
};

export default async function LearningPage() {
  const [portfolio, gateBlocks] = await Promise.all([
    readPortfolio(),
    readGateBlocks(2000),
  ]);

  const stats = computeHitStats(
    portfolio.closed_trades ?? [],
    portfolio.cash_movements ?? [],
  );
  const preMortem = preMortemAccuracy(portfolio.closed_trades ?? []);
  const days = recentGateDays(gateBlocks, 7);
  const gateDays = days
    .map((d) => summarizeGateActivityDay(gateBlocks, d))
    .filter((d): d is NonNullable<typeof d> => d !== null);

  const cal = stats?.calibration ?? null;
  const mistakes = stats?.mistake_classes ?? {};
  const mistakeRows = Object.entries(mistakes).sort((a, b) => b[1] - a[1]);
  const nClosed = (portfolio.closed_trades ?? []).length;

  return (
    <>
      <header className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-lg font-semibold">Learning · Bot-Review</h1>
          <p className="text-xs text-zinc-500">
            Trade-Learning (Mistakes, Kalibrierung) + Gate-Activity-Anomalien
            der letzten 7 Tage. Flags = Bot-Logik-Auffälligkeiten für Review.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <a
            href="/"
            className="text-xs px-3 py-1.5 rounded border border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          >
            ← Dashboard
          </a>
          <a
            href="/brain"
            className="text-xs px-3 py-1.5 rounded border border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          >
            Brain →
          </a>
        </div>
      </header>

      <main className="p-6 max-w-5xl mx-auto flex flex-col gap-6">
        {/* ---------- Trade-Learning ---------- */}
        <section className="rounded-lg border border-zinc-800 bg-zinc-900/40">
          <div className="px-4 py-3 border-b border-zinc-800">
            <h2 className="text-sm font-semibold">🎯 Trade-Learning</h2>
          </div>
          <div className="p-4">
            {!stats ? (
              <p className="text-sm text-zinc-500">
                Sample zu klein ({nClosed} closed, &lt;3) — noch nichts zu
                lernen.
              </p>
            ) : (
              <div className="flex flex-col gap-4">
                <div>
                  <div className="text-xs text-zinc-500 mb-1.5">
                    Mistake-Klassen (letzte 20 Losses)
                  </div>
                  {mistakeRows.length === 0 ? (
                    <p className="text-sm text-zinc-500">
                      Keine getaggten Losses.
                    </p>
                  ) : (
                    <div className="flex gap-3 flex-wrap">
                      {mistakeRows.map(([cls, n]) => (
                        <span
                          key={cls}
                          className="text-sm tabular-nums rounded border border-zinc-800 bg-zinc-900 px-2.5 py-1"
                        >
                          <span
                            className={MISTAKE_COLORS[cls] ?? "text-zinc-300"}
                          >
                            {cls}
                          </span>{" "}
                          <span className="text-zinc-100 font-semibold">
                            {n}
                          </span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                <div>
                  <div className="text-xs text-zinc-500 mb-1.5">
                    Kalibrierung (Brier-Score, rolling 20)
                  </div>
                  {!cal ? (
                    <p className="text-sm text-zinc-500">
                      Noch keine gescorten Trades (p_win + Brier).
                    </p>
                  ) : (
                    <div className="flex gap-3 flex-wrap text-sm tabular-nums">
                      <Metric label="n" value={String(cal.n)} />
                      <Metric
                        label="Bias"
                        value={`${cal.bias >= 0 ? "+" : ""}${cal.bias.toFixed(2)}`}
                        tone={Math.abs(cal.bias) >= 0.05 ? "warn" : "good"}
                      />
                      <Metric
                        label="Brier"
                        value={cal.avg_brier.toFixed(3)}
                      />
                      <Metric
                        label="Haircut"
                        value={
                          cal.haircut
                            ? `${cal.haircut >= 0 ? "+" : ""}${cal.haircut.toFixed(2)} aktiv`
                            : cal.n < MIN_CALIBRATION_N
                              ? `inaktiv (n<${MIN_CALIBRATION_N})`
                              : "0 (kalibriert)"
                        }
                        tone={cal.haircut ? "warn" : "good"}
                      />
                    </div>
                  )}
                </div>

                <div>
                  <div className="text-xs text-zinc-500 mb-1.5">
                    Pre-Mortem-Accuracy
                  </div>
                  {!preMortem ? (
                    <p className="text-sm text-zinc-500">
                      Zu wenig getaggte Losses (&lt;3) für Pre-Mortem-Scoring.
                    </p>
                  ) : (
                    <p className="text-sm tabular-nums">
                      <span className="text-zinc-100 font-semibold">
                        {preMortem.accuracy_pct}%
                      </span>{" "}
                      <span className="text-zinc-500">
                        ({preMortem.correct}/{preMortem.n} top_fail_mode korrekt
                        antizipiert)
                      </span>
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        </section>

        {/* ---------- Gate-Activity ---------- */}
        <section className="rounded-lg border border-zinc-800 bg-zinc-900/40">
          <div className="px-4 py-3 border-b border-zinc-800">
            <h2 className="text-sm font-semibold">
              🔍 Gate-Activity · letzte 7 Tage
            </h2>
          </div>
          <div className="p-4">
            {gateDays.length === 0 ? (
              <p className="text-sm text-zinc-500">
                Keine Gate-Events protokolliert.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {gateDays.map((d) => (
                  <div
                    key={d.day}
                    className="rounded border border-zinc-800 bg-zinc-900/60 p-3"
                  >
                    <div className="flex items-baseline justify-between gap-3 flex-wrap">
                      <span className="text-sm font-semibold tabular-nums">
                        {d.day}
                      </span>
                      <span className="text-xs text-zinc-500 tabular-nums">
                        {d.nBlocks} blocks · {d.nPasses} passes
                      </span>
                    </div>
                    {d.byGate.length > 0 && (
                      <div className="mt-2 flex gap-2 flex-wrap">
                        {d.byGate.map((g) => (
                          <span
                            key={g.gate}
                            className="text-xs tabular-nums rounded bg-zinc-800/80 text-zinc-400 px-2 py-0.5"
                          >
                            {g.gate} ×{g.count}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="mt-2 flex flex-col gap-1">
                      {d.flags.length === 0 ? (
                        <span className="text-xs text-emerald-400">
                          ✅ keine Anomalien
                        </span>
                      ) : (
                        d.flags.map((f, i) => (
                          <span
                            key={i}
                            className="text-xs text-amber-400 bg-amber-400/10 rounded px-2 py-1"
                          >
                            ⚠️ {f}
                          </span>
                        ))
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </main>
    </>
  );
}

function Metric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "good" | "warn";
}) {
  const valueCls =
    tone === "warn"
      ? "text-amber-400"
      : tone === "good"
        ? "text-emerald-400"
        : "text-zinc-100";
  return (
    <span className="rounded border border-zinc-800 bg-zinc-900 px-2.5 py-1">
      <span className="text-zinc-500">{label} </span>
      <span className={`font-semibold ${valueCls}`}>{value}</span>
    </span>
  );
}
