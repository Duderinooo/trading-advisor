"use client";

import { useEffect, useState } from "react";
import type { Heartbeat as HeartbeatT } from "@/lib/types";

export default function Heartbeat({
  heartbeat,
  killSwitchActive,
  killSwitchReason,
  killSwitchTs,
  ddHaltActive,
}: {
  heartbeat?: HeartbeatT;
  killSwitchActive?: boolean;
  killSwitchReason?: string | null;
  killSwitchTs?: string | null;
  ddHaltActive?: boolean;
}) {
  const [ageSec, setAgeSec] = useState<number | null>(null);

  useEffect(() => {
    if (!heartbeat?.last_tick) return;
    const tick = () => {
      const last = new Date(heartbeat.last_tick.replace(" ", "T"));
      const delta = Math.floor((Date.now() - last.getTime()) / 1000);
      setAgeSec(delta);
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => clearInterval(id);
  }, [heartbeat?.last_tick]);

  if (!heartbeat) {
    return (
      <div className="text-sm text-zinc-500">
        Kein Heartbeat. Bot läuft entweder nicht oder Version &lt; heartbeat-feature.
      </div>
    );
  }

  const stale = ageSec !== null && heartbeat.market_hours && ageSec > 120;
  const veryStale = ageSec !== null && ageSec > 600;

  const dotTone = veryStale
    ? "bg-rose-500"
    : stale
      ? "bg-amber-500"
      : "bg-emerald-500";

  const ageStr =
    ageSec === null
      ? "—"
      : ageSec < 60
        ? `${ageSec}s`
        : ageSec < 3600
          ? `${Math.floor(ageSec / 60)}min`
          : `${Math.floor(ageSec / 3600)}h ${Math.floor((ageSec % 3600) / 60)}min`;

  const apiPct = (heartbeat.api_calls_today / heartbeat.api_cap) * 100;
  const apiTone =
    apiPct > 80
      ? "text-rose-400"
      : apiPct > 50
        ? "text-amber-400"
        : "text-zinc-100";

  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center gap-3">
        <span className={`inline-block w-2.5 h-2.5 rounded-full ${dotTone}`} />
        <div className="flex-1">
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Last Tick
          </div>
          <div className="text-lg font-semibold">
            <span suppressHydrationWarning>{ageStr}</span>{" "}
            <span className="text-xs text-zinc-500">ago</span>
          </div>
          <div className="text-xs text-zinc-500">{heartbeat.last_tick}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Market
          </div>
          <div
            className={`text-sm font-medium ${heartbeat.market_hours ? "text-emerald-400" : "text-zinc-500"}`}
          >
            {heartbeat.market_hours ? "OPEN" : "closed"}
          </div>
        </div>
      </div>

      <div>
        <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">
          API Calls Today
        </div>
        <div className="flex items-baseline gap-2">
          <span className={`text-lg font-semibold ${apiTone}`}>
            {heartbeat.api_calls_today}
          </span>
          <span className="text-xs text-zinc-500">
            / {heartbeat.api_cap} cap
          </span>
        </div>
        <div className="mt-1 h-1.5 rounded bg-zinc-800 overflow-hidden">
          <div
            className={`h-full ${apiPct > 80 ? "bg-rose-500" : apiPct > 50 ? "bg-amber-500" : "bg-emerald-500"}`}
            style={{ width: `${Math.min(100, apiPct)}%` }}
          />
        </div>
      </div>

      {(killSwitchActive || ddHaltActive) && (
        <div className="space-y-1.5 pt-2 border-t border-zinc-800">
          {killSwitchActive && (
            <div className="text-xs">
              <span className="text-rose-400 font-semibold">KILL-SWITCH</span>{" "}
              <span className="text-zinc-400">{killSwitchReason ?? ""}</span>
              {killSwitchTs && (
                <span className="text-zinc-500"> · seit {killSwitchTs}</span>
              )}
            </div>
          )}
          {ddHaltActive && (
            <div className="text-xs">
              <span className="text-amber-400 font-semibold">DD-HALT</span>{" "}
              <span className="text-zinc-400">
                Drawdown latch — keine neuen Entries bis Recovery.
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
