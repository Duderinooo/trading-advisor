"use client";

import { useState } from "react";
import type { ProposedTrade } from "@/lib/types";

function Stat({
  label,
  value,
  sub,
  tone = "text-zinc-100",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</span>
      <span className={`font-mono text-sm ${tone}`}>{value}</span>
      {sub && <span className="text-[10px] text-zinc-500">{sub}</span>}
    </div>
  );
}

function Chip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
        ok
          ? "border-emerald-800 bg-emerald-900/20 text-emerald-300"
          : "border-rose-800 bg-rose-900/20 text-rose-300"
      }`}
    >
      {ok ? "✓" : "✗"} {label}
    </span>
  );
}

type Status = "idle" | "sending" | "sent" | "error";

function Card({ p }: { p: ProposedTrade }) {
  const pl = p.plan;
  const [entry, setEntry] = useState(pl ? String(pl.entry) : "");
  const [shares, setShares] = useState(pl ? String(pl.shares) : "");
  const [status, setStatus] = useState<Status>("idle");
  const [msg, setMsg] = useState("");

  if (!pl) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm">
        <span className="font-semibold text-zinc-200">{p.ticker}</span>
        <span className="ml-2 text-zinc-500">— keine Live-Daten, Plan ausstehend</span>
      </div>
    );
  }
  const [tp1, tp2] = pl.take_profit;
  const [r1, r2] = pl.tp_r;
  const allOk = pl.gates.fee_ok && pl.gates.whole_share_ok && pl.gates.affordable;
  const sig = pl.signals;

  async function send(action: "fire" | "cancel") {
    setStatus("sending");
    setMsg("");
    const body: Record<string, unknown> = { action, ticker: p.ticker };
    if (action === "fire") {
      body.entry_price = Number(entry);
      body.shares = Number(shares);
    }
    try {
      const res = await fetch("/api/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        setStatus("sent");
        setMsg(action === "fire" ? "⏳ Bot führt aus…" : "✗ verworfen");
      } else {
        setStatus("error");
        setMsg(data.error ?? `HTTP ${res.status}`);
      }
    } catch (e) {
      setStatus("error");
      setMsg(String(e));
    }
  }

  const liveInvest = (Number(entry) || 0) * (Number(shares) || 0);
  const busy = status === "sending" || status === "sent";

  return (
    <div
      className={`rounded-lg border bg-zinc-900/40 overflow-hidden ${
        allOk ? "border-zinc-700" : "border-rose-900/60"
      }`}
    >
      {/* Header */}
      <div className="flex items-baseline gap-2 px-4 pt-3">
        <span className="text-base font-semibold text-zinc-100">{p.ticker}</span>
        <span className="text-xs text-zinc-400">{pl.setup_type}</span>
        {p.source === "manual" && (
          <span className="text-[10px] uppercase tracking-wider text-amber-400">manual</span>
        )}
        <span className="ml-auto text-xs text-zinc-500">
          €{liveInvest.toFixed(0)} · Cash €{(pl.cash_left_eur + pl.invest_eur - liveInvest).toFixed(0)}
        </span>
      </div>

      {/* Price grid — Entry + Shares editable */}
      <div className="grid grid-cols-4 gap-2 px-4 py-3">
        <label className="flex flex-col">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500">Entry €</span>
          <input
            type="number"
            step="0.01"
            value={entry}
            disabled={busy}
            onChange={(e) => setEntry(e.target.value)}
            className="w-full bg-zinc-800/60 border border-zinc-700 rounded px-1.5 py-0.5 font-mono text-sm text-zinc-100 focus:border-sky-600 focus:outline-none disabled:opacity-50"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500">Stück</span>
          <input
            type="number"
            step="1"
            value={shares}
            disabled={busy}
            onChange={(e) => setShares(e.target.value)}
            className="w-full bg-zinc-800/60 border border-zinc-700 rounded px-1.5 py-0.5 font-mono text-sm text-zinc-100 focus:border-sky-600 focus:outline-none disabled:opacity-50"
          />
        </label>
        <Stat label="TP1" value={`€${tp1.toFixed(2)}`} sub={`${r1}R · €${pl.net_tp1_eur}`} tone="text-emerald-300" />
        <Stat label="TP2" value={`€${tp2.toFixed(2)}`} sub={`${r2}R`} tone="text-emerald-300" />
      </div>

      {/* Stop + risk + gates */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-zinc-800 px-4 py-2">
        <span className="font-mono text-xs text-rose-300">
          SL €{pl.stop_loss.toFixed(2)}
          {pl.sl_dist_atr != null && (
            <span className="text-zinc-500"> ({pl.sl_dist_atr}×ATR{pl.sl_clamped ? " ⚠" : ""})</span>
          )}
        </span>
        <span className="font-mono text-xs text-zinc-300">
          Risiko €{pl.risk_eur.toFixed(2)}
          {pl.risk_pct_capital != null && <span className="text-zinc-500"> ({pl.risk_pct_capital}%)</span>}
        </span>
        <div className="ml-auto flex gap-1.5">
          <Chip ok={pl.gates.fee_ok} label="fee" />
          <Chip ok={pl.gates.whole_share_ok} label="ganze stk" />
          <Chip ok={pl.gates.affordable} label="≤cap" />
        </div>
      </div>

      {/* Signals + thesis */}
      <div className="border-t border-zinc-800 px-4 py-2">
        <div className="text-[11px] text-zinc-500">
          {[
            sig.base_quality_score != null && `bq ${sig.base_quality_score}/10`,
            sig.higher_lows_5d != null && `HL ${sig.higher_lows_5d}`,
            sig.rsi14 != null && `RSI ${sig.rsi14.toFixed(0)}`,
            sig.wk_trend && `wk ${sig.wk_trend}`,
            sig.rs_20d_vs_index_pct != null &&
              `RS ${sig.rs_20d_vs_index_pct > 0 ? "+" : ""}${sig.rs_20d_vs_index_pct.toFixed(1)}`,
          ]
            .filter(Boolean)
            .join("  ·  ")}
        </div>
        {p.thesis && <div className="mt-1 text-[11px] italic text-zinc-500">{p.thesis}</div>}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 border-t border-zinc-800 px-4 py-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => send("fire")}
          className="flex-1 rounded bg-emerald-700/80 hover:bg-emerald-600 disabled:opacity-40 text-emerald-50 text-sm font-semibold py-1.5 transition-colors"
        >
          ✓ Fire
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => send("cancel")}
          className="rounded border border-rose-800 hover:bg-rose-900/30 disabled:opacity-40 text-rose-300 text-sm px-4 py-1.5 transition-colors"
        >
          ✗
        </button>
        {msg && (
          <span className={`text-xs ${status === "error" ? "text-rose-400" : "text-zinc-400"}`}>
            {msg}
          </span>
        )}
      </div>
    </div>
  );
}

export default function ProposedTrades({ proposals }: { proposals: ProposedTrade[] }) {
  if (!proposals || proposals.length === 0) {
    return <div className="text-sm text-zinc-500">Keine offenen Trade-Vorschläge.</div>;
  }
  return (
    <div className="space-y-3">
      {proposals.map((p) => (
        <Card key={p.ticker} p={p} />
      ))}
    </div>
  );
}
