"use client";

import { useEffect, useState } from "react";
import type { Heartbeat as HeartbeatT } from "@/lib/types";

type RestartState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "ok" }
  | { kind: "err"; msg: string };

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
  const [restart, setRestart] = useState<RestartState>({ kind: "idle" });

  const onRestart = async () => {
    if (
      !window.confirm(
        "Bot wirklich neustarten? launchctl unload+load — KeepAlive=true sorgt für sauberen Respawn.",
      )
    ) {
      return;
    }
    setRestart({ kind: "running" });
    try {
      const res = await fetch("/api/bot/restart", { method: "POST" });
      const data = await res.json();
      if (res.ok && data.ok) {
        setRestart({ kind: "ok" });
      } else {
        setRestart({ kind: "err", msg: data.error ?? `HTTP ${res.status}` });
      }
    } catch (e) {
      setRestart({ kind: "err", msg: String(e) });
    }
  };

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

      <div className="pt-2 border-t border-zinc-800 space-y-1.5">
        <button
          type="button"
          onClick={onRestart}
          disabled={restart.kind === "running"}
          className="text-xs px-2.5 py-1 rounded border border-zinc-700 hover:border-zinc-500 hover:bg-zinc-800 disabled:opacity-50 disabled:cursor-not-allowed"
          title="Bot via launchctl neustarten (atomic, keine Race)"
        >
          {restart.kind === "running" ? "Neustart…" : "Bot neustarten"}
        </button>
        {restart.kind === "ok" && (
          <div className="text-xs text-emerald-400">Neu gestartet via launchctl</div>
        )}
        {restart.kind === "err" && (
          <div className="text-xs text-rose-400">Fehler: {restart.msg}</div>
        )}
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
