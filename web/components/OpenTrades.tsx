"use client";

import { Fragment, useState } from "react";
import type { OpenTrade } from "@/lib/types";

function tpFmt(tp: OpenTrade["take_profit"]) {
  if (tp == null) return "—";
  if (Array.isArray(tp)) return tp.map((x) => x.toFixed(2)).join(" / ");
  return tp.toFixed(2);
}

function fmtTpHistory(tp: number | number[] | null | undefined) {
  if (tp == null) return "—";
  if (Array.isArray(tp)) return tp.map((x) => x.toFixed(2)).join(" / ");
  return tp.toFixed(2);
}

export default function OpenTrades({ trades }: { trades: OpenTrade[] }) {
  const [openHistory, setOpenHistory] = useState<Record<string, boolean>>({});

  if (trades.length === 0) {
    return (
      <div className="text-sm text-zinc-500">Keine offenen Positionen.</div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
            <th className="py-2 pr-3"></th>
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
            const adds = t.add_history ?? [];
            const updates = t.update_history ?? [];
            const histCount = adds.length + updates.length;
            const key = `${t.ticker}-${i}`;
            const isOpen = openHistory[key] ?? false;
            return (
              <Fragment key={key}>
                <tr className="border-b border-zinc-900/60 hover:bg-zinc-900/30">
                  <td className="py-2 pr-2">
                    {histCount > 0 ? (
                      <button
                        type="button"
                        onClick={() =>
                          setOpenHistory((s) => ({ ...s, [key]: !s[key] }))
                        }
                        className="text-xs text-zinc-500 hover:text-zinc-200"
                        title={`${histCount} Verlaufseintrag/-träge`}
                      >
                        {isOpen ? "▾" : "▸"} {histCount}
                      </button>
                    ) : null}
                  </td>
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
                {isOpen && (
                  <tr className="border-b border-zinc-900/60">
                    <td colSpan={10} className="py-2 px-3 bg-zinc-950/50">
                      <div className="space-y-2 text-xs">
                        {adds.length > 0 && (
                          <div>
                            <div className="font-semibold text-cyan-400 mb-1">
                              ADDs ({adds.length})
                            </div>
                            <ul className="space-y-1">
                              {adds.map((a, ai) => (
                                <li
                                  key={ai}
                                  className="text-zinc-400 font-mono"
                                >
                                  <span className="text-zinc-500">
                                    {a.date}
                                  </span>{" "}
                                  · +{a.added_shares} × €{a.fill_price.toFixed(2)}{" "}
                                  = €{a.added_size_eur.toFixed(2)}
                                  {a.source && (
                                    <span className="text-zinc-600">
                                      {" "}
                                      [{a.source}]
                                    </span>
                                  )}
                                  {(a.trigger || a.thesis_reinforcement) && (
                                    <div className="text-zinc-500 italic ml-4">
                                      {a.trigger || a.thesis_reinforcement}
                                    </div>
                                  )}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {updates.length > 0 && (
                          <div>
                            <div className="font-semibold text-amber-400 mb-1">
                              UPDATEs ({updates.length})
                            </div>
                            <ul className="space-y-1">
                              {updates.map((u, ui) => (
                                <li key={ui} className="text-zinc-400 font-mono">
                                  <span className="text-zinc-500">
                                    {u.date}
                                  </span>
                                  {u.new_sl != null && (
                                    <>
                                      {" "}
                                      · SL{" "}
                                      {u.old_sl != null
                                        ? u.old_sl.toFixed(2)
                                        : "–"}
                                      →{u.new_sl.toFixed(2)}
                                    </>
                                  )}
                                  {u.new_tp != null && (
                                    <>
                                      {" "}
                                      · TP {fmtTpHistory(u.old_tp)}→
                                      {fmtTpHistory(u.new_tp)}
                                    </>
                                  )}
                                  {u.reason && (
                                    <div className="text-zinc-500 italic ml-4">
                                      {u.reason}
                                    </div>
                                  )}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
