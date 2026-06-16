"use client";

import type { ProposedTrade } from "@/lib/types";

function Gate({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${
        ok
          ? "bg-emerald-900/30 text-emerald-300 border border-emerald-800"
          : "bg-rose-900/30 text-rose-300 border border-rose-800"
      }`}
    >
      {ok ? "✓" : "✗"} {label}
    </span>
  );
}

function Card({ p }: { p: ProposedTrade }) {
  const pl = p.plan;
  if (!pl) {
    return (
      <li className="px-3 py-2 rounded border border-zinc-700 bg-zinc-800/30 text-sm">
        <span className="font-semibold">{p.ticker}</span>
        <span className="ml-2 text-zinc-500">— keine Live-Daten, Plan ausstehend</span>
      </li>
    );
  }
  const [tp1, tp2] = pl.take_profit;
  const [r1, r2] = pl.tp_r;
  return (
    <li className="px-3 py-2.5 rounded border border-zinc-700 bg-zinc-800/30 text-sm space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="font-semibold text-zinc-100">{p.ticker}</span>
        <span className="text-xs text-zinc-400">{pl.setup_type}</span>
        {p.source === "manual" && (
          <span className="text-[10px] uppercase tracking-wider text-amber-400">manual</span>
        )}
        <span className="ml-auto font-mono text-zinc-300">
          {pl.shares} Stk @ €{pl.entry.toFixed(2)} = €{pl.invest_eur.toFixed(0)}
        </span>
      </div>

      <div className="font-mono text-xs text-zinc-400">
        SL €{pl.stop_loss.toFixed(2)}
        {pl.sl_dist_atr != null && <span> ({pl.sl_dist_atr}×ATR{pl.sl_clamped ? `, ${pl.sl_clamped}` : ""})</span>}
        {" · "}Risiko €{pl.risk_eur.toFixed(2)}
        {pl.risk_pct_capital != null && <span> ({pl.risk_pct_capital}%)</span>}
        {" · "}Cash übrig €{pl.cash_left_eur.toFixed(0)}
      </div>

      <div className="font-mono text-xs text-zinc-400">
        TP1 €{tp1.toFixed(2)} ({r1}R) → brutto €{pl.gross_tp1_eur} / netto €{pl.net_tp1_eur}
        {" · "}TP2 €{tp2.toFixed(2)} ({r2}R) → brutto €{pl.gross_tp2_eur}
      </div>

      <div className="flex flex-wrap gap-1.5 pt-0.5">
        <Gate ok={pl.gates.fee_ok} label="fee" />
        <Gate ok={pl.gates.whole_share_ok} label="ganze stk" />
        <Gate ok={pl.gates.affordable} label="≤cap" />
      </div>

      <div className="text-[11px] text-zinc-500">
        {[
          pl.signals.base_quality_score != null && `bq ${pl.signals.base_quality_score}/10`,
          pl.signals.higher_lows_5d != null && `HL ${pl.signals.higher_lows_5d}`,
          pl.signals.rsi14 != null && `RSI ${pl.signals.rsi14.toFixed(0)}`,
          pl.signals.wk_trend && `wk ${pl.signals.wk_trend}`,
          pl.signals.rs_20d_vs_index_pct != null &&
            `RS ${pl.signals.rs_20d_vs_index_pct > 0 ? "+" : ""}${pl.signals.rs_20d_vs_index_pct.toFixed(1)}`,
        ]
          .filter(Boolean)
          .join(" · ")}
      </div>

      {p.thesis && <div className="text-[11px] italic text-zinc-500">{p.thesis}</div>}
    </li>
  );
}

export default function ProposedTrades({ proposals }: { proposals: ProposedTrade[] }) {
  if (!proposals || proposals.length === 0) {
    return <div className="text-sm text-zinc-500">Keine offenen Trade-Vorschläge.</div>;
  }
  return (
    <ul className="space-y-2">
      {proposals.map((p) => (
        <Card key={p.ticker} p={p} />
      ))}
    </ul>
  );
}
