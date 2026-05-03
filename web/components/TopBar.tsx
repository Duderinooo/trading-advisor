"use client";

import type { ReactNode } from "react";

function signCls(n: number): string {
  return n > 0 ? "num-pos" : n < 0 ? "num-neg" : "num-flat";
}

function fmtPct(n: number, d = 2): string {
  return (n >= 0 ? "+" : "") + n.toFixed(d) + "%";
}

function fmtEur(n: number, d = 2): string {
  return (
    (n < 0 ? "-" : "") +
    "€" +
    Math.abs(n).toLocaleString("de-DE", {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    })
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M2 8a6 6 0 0 1 10.5-4M14 3v3.5h-3.5M14 8a6 6 0 0 1-10.5 4M2 13V9.5h3.5" />
    </svg>
  );
}

export default function TopBar({
  equityLive,
  todayPct,
  drawdownPct,
  killSwitch,
  killSwitchReason,
  ddHalt,
  dailyPnlPct,
  heatPct,
  openCount,
  maxPositions,
  apiCalls,
  apiCap,
  staleSecs,
  refreshing,
  onRefresh,
  refreshedLabel,
}: {
  equityLive: number;
  todayPct: number;
  drawdownPct: number;
  killSwitch: boolean;
  killSwitchReason?: string;
  ddHalt: boolean;
  dailyPnlPct: number;
  heatPct: number;
  openCount: number;
  maxPositions: number;
  apiCalls: number;
  apiCap: number;
  staleSecs: number | null;
  refreshing: boolean;
  onRefresh: () => void;
  refreshedLabel: ReactNode;
}) {
  const stale = staleSecs != null && staleSecs > 600;
  const heatTone = heatPct > 70 ? "bad" : heatPct > 50 ? "warn" : "good";
  const pnlClass = dailyPnlPct >= 0 ? "pill-pnl-pos" : "pill-pnl-neg";

  return (
    <div className="topbar" data-stale={stale}>
      <div className="topbar-inner">
        <div className="brand">
          <div className="brand-mark">T</div>
          <span>Trading Advisor</span>
          <span className="brand-sub">€1k acct</span>
        </div>

        <div className="topbar-stats">
          <div className="topbar-stat">
            <span className="topbar-stat-label">Equity (Live)</span>
            <span className={`topbar-stat-value big ${signCls(todayPct)}`}>
              {fmtEur(equityLive)}
            </span>
          </div>
          <div className="topbar-stat hide-mobile">
            <span className="topbar-stat-label">Today</span>
            <span className={`topbar-stat-value ${signCls(todayPct)}`}>
              {fmtPct(todayPct)}
            </span>
          </div>
          <div className="topbar-stat hide-mobile">
            <span className="topbar-stat-label">Drawdown</span>
            <span
              className={`topbar-stat-value ${drawdownPct > 0 ? "num-neg" : "num-flat"}`}
            >
              -{drawdownPct.toFixed(2)}%
            </span>
          </div>

          <div className="topbar-pills">
            <span
              className={`pill ${killSwitch ? "bad" : "good"}`}
              title={killSwitch && killSwitchReason ? killSwitchReason : undefined}
            >
              <span className="dot" />
              kill {killSwitch ? "ON" : "off"}
            </span>
            <span className={`pill ${ddHalt ? "bad" : "good"}`}>
              <span className="dot" />
              dd-halt {ddHalt ? "ON" : "off"}
            </span>
            <span className={`pill mono ${pnlClass}`}>
              <span className="dot" />
              {fmtPct(dailyPnlPct, 2)}
            </span>
            <span className={`pill ${heatTone}`}>
              <span className="dot" />
              heat {heatPct.toFixed(1)}%
            </span>
            <span className="pill mono neutral hide-mobile">
              open {openCount}/{maxPositions}
            </span>
            {stale && staleSecs != null && (
              <span className="pill warn">
                <span className="dot" />
                STALE {Math.floor(staleSecs / 60)}m
              </span>
            )}
          </div>
        </div>

        <div className="topbar-actions">
          <span
            className="pill mono neutral hide-mobile"
            title={`API ${apiCalls}/${apiCap}`}
          >
            api {apiCalls}/{apiCap}
          </span>
          <button
            type="button"
            className="icon-btn"
            onClick={onRefresh}
            disabled={refreshing}
            title="Refresh"
          >
            {refreshing ? <span className="icon-btn-mono">…</span> : <RefreshIcon />}
          </button>
          <span className="pill mono neutral hide-mobile pill-fixed">
            {refreshedLabel}
          </span>
          <span className="topbar-divider" />
          <a href="/brain" className="icon-btn" title="/brain">
            <span className="icon-btn-mono">BR</span>
          </a>
          <a href="/logs" className="icon-btn" title="/logs">
            <span className="icon-btn-mono">LG</span>
          </a>
        </div>
      </div>
    </div>
  );
}

export { fmtEur, fmtPct, signCls };
