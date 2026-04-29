type Pill = {
  label: string;
  value: string;
  tone: "good" | "warn" | "bad" | "neutral";
};

function pillCls(tone: Pill["tone"]) {
  switch (tone) {
    case "good":
      return "bg-emerald-900/40 text-emerald-300 border-emerald-800";
    case "warn":
      return "bg-amber-900/40 text-amber-300 border-amber-800";
    case "bad":
      return "bg-rose-900/40 text-rose-300 border-rose-800";
    default:
      return "bg-zinc-900/60 text-zinc-300 border-zinc-800";
  }
}

export default function HealthStatus({
  killSwitch,
  killSwitchReason,
  ddHalt,
  dailyLossPct,
  heatPct,
  openCount,
  pendingCount,
  watchCount,
}: {
  killSwitch?: boolean;
  killSwitchReason?: string;
  ddHalt?: boolean;
  dailyLossPct: number;
  heatPct: number;
  openCount: number;
  pendingCount: number;
  watchCount: number;
}) {
  const pills: Pill[] = [
    {
      label: "Kill-Switch",
      value: killSwitch ? `🛑 AKTIV${killSwitchReason ? ` (${killSwitchReason})` : ""}` : "✅ aus",
      tone: killSwitch ? "bad" : "good",
    },
    {
      label: "DD-Halt",
      value: ddHalt ? "🚫 LATCH" : "✅ —",
      tone: ddHalt ? "warn" : "good",
    },
    {
      label: "Daily P&L",
      value: `${dailyLossPct >= 0 ? "+" : ""}${dailyLossPct.toFixed(2)}%`,
      tone:
        dailyLossPct <= -5
          ? "bad"
          : dailyLossPct <= -2
            ? "warn"
            : dailyLossPct >= 0
              ? "good"
              : "neutral",
    },
    {
      label: "Heat",
      value: `${heatPct.toFixed(1)}%`,
      tone:
        heatPct >= 10 ? "bad" : heatPct >= 7 ? "warn" : "good",
    },
    {
      label: "Open",
      value: `${openCount}/5`,
      tone: openCount >= 5 ? "warn" : "neutral",
    },
    {
      label: "Watchlevels",
      value: `${watchCount}`,
      tone: watchCount === 0 ? "warn" : "good",
    },
    {
      label: "Pending",
      value: `${pendingCount}`,
      tone: pendingCount > 0 ? "warn" : "neutral",
    },
  ];

  return (
    <div className="flex flex-wrap gap-2">
      {pills.map((p) => (
        <div
          key={p.label}
          className={`flex items-center gap-2 px-3 py-1.5 rounded border text-xs ${pillCls(p.tone)}`}
        >
          <span className="text-zinc-500 uppercase tracking-wider">
            {p.label}
          </span>
          <span className="font-mono">{p.value}</span>
        </div>
      ))}
    </div>
  );
}
