"use client";

import { useEffect, useState } from "react";

type EarningsEvent = {
  ticker: string;
  earnings_date: string;
  days_until: number;
};

type Payload = {
  generated_at: string | null;
  events: EarningsEvent[];
};

export default function EarningsCalendar() {
  const [data, setData] = useState<Payload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/earnings", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        setData((await res.json()) as Payload);
      } catch (e) {
        setErr((e as Error).message);
      }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (err) return <div className="text-sm text-rose-400">Load failed: {err}</div>;
  if (!data) return <div className="text-sm text-zinc-500">Lade…</div>;
  if (!data.events.length) {
    return (
      <div className="text-sm text-zinc-500">
        Keine Earnings in den nächsten 14 Tagen für Watchlist + Open Positions.
      </div>
    );
  }

  return (
    <table className="w-full text-sm">
      <thead className="text-zinc-500 text-xs uppercase tracking-wide">
        <tr>
          <th className="text-left py-1">Ticker</th>
          <th className="text-left py-1">Date</th>
          <th className="text-right py-1">In</th>
        </tr>
      </thead>
      <tbody>
        {data.events.map((e) => (
          <tr key={`${e.ticker}-${e.earnings_date}`} className="border-t border-zinc-800/50">
            <td className="py-1.5">{e.ticker}</td>
            <td className="py-1.5">{e.earnings_date}</td>
            <td
              className={`py-1.5 text-right ${
                e.days_until <= 2
                  ? "text-rose-400 font-semibold"
                  : e.days_until <= 5
                    ? "text-amber-400"
                    : "text-zinc-400"
              }`}
            >
              {e.days_until}d
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
