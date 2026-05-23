"use client";

import { useEffect, useState } from "react";

type Shadow = {
  tested_count: number;
  would_skip_count: number;
  would_skip_pnl_eur: number;
  kept_count: number;
  kept_pnl_eur: number;
  net_delta_eur: number;
  overrides: Record<string, number>;
};

type Analytics = {
  shadow_what_if?: { generated_at: string; data: Shadow | Record<string, never> } | null;
};

export default function ShadowDelta() {
  const [data, setData] = useState<Shadow | null | "empty">(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/analytics", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const all = (await res.json()) as Analytics;
        const raw = all.shadow_what_if?.data;
        if (!raw || !("tested_count" in raw)) {
          setData("empty");
        } else {
          setData(raw as Shadow);
        }
      } catch (e) {
        setErr((e as Error).message);
      }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (err) return <div className="text-sm text-rose-400">Load failed: {err}</div>;
  if (data === null) return <div className="text-sm text-zinc-500">Lade…</div>;
  if (data === "empty") {
    return (
      <div className="text-sm text-zinc-500">
        Kein Shadow-Override aktiv. Setze einen Wert in <code>config/shadow.py</code>{" "}
        (z.B. <code>MIN_EXPECTED_EDGE: 0.06</code>) um das What-If zu sehen.
      </div>
    );
  }
  const skipPnlPos = data.would_skip_pnl_eur >= 0;
  const kept = data.kept_pnl_eur;
  return (
    <div className="text-sm space-y-2">
      <div className="text-zinc-500 text-xs">
        Override:{" "}
        {Object.entries(data.overrides)
          .map(([k, v]) => `${k}=${v}`)
          .join(", ")}
      </div>
      <div className="flex gap-6 flex-wrap">
        <div>
          <div className="text-zinc-500 text-xs">Tested</div>
          <div className="font-mono">{data.tested_count}</div>
        </div>
        <div>
          <div className="text-zinc-500 text-xs">Would-skip</div>
          <div className="font-mono">{data.would_skip_count}</div>
        </div>
        <div>
          <div className="text-zinc-500 text-xs">
            Skipped PnL (was lost)
          </div>
          <div
            className={`font-mono ${
              skipPnlPos ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {skipPnlPos ? "+" : ""}€{data.would_skip_pnl_eur.toFixed(2)}
          </div>
        </div>
        <div>
          <div className="text-zinc-500 text-xs">Kept PnL (would still trade)</div>
          <div className={`font-mono ${kept >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
            {kept >= 0 ? "+" : ""}€{kept.toFixed(2)}
          </div>
        </div>
      </div>
      <div className="text-zinc-500 text-xs">
        Lesart: hohe „skipped PnL" Negativ = Shadow vermeidet Verluste. Positiv =
        Shadow verpasst Gewinne.
      </div>
    </div>
  );
}
