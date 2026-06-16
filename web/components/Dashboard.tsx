"use client";

import BacktestReport from "@/components/BacktestReport";
import BotActivityTimeline from "@/components/BotActivityTimeline";
import CalibrationCurve from "@/components/CalibrationCurve";
import { Card, Kpi } from "@/components/Card";
import ClosedTrades from "@/components/ClosedTrades";
import CollapsibleSection from "@/components/CollapsibleSection";
import CorrelationHeatmap from "@/components/CorrelationHeatmap";
import DrawdownTrajectory from "@/components/DrawdownTrajectory";
import EarningsCalendar from "@/components/EarningsCalendar";
import EquityChart from "@/components/EquityChart";
import GateAttribution from "@/components/GateAttribution";
import Heartbeat from "@/components/Heartbeat";
import HitRateTrend from "@/components/HitRateTrend";
import MaeMfe from "@/components/MaeMfe";
import MistakeTrend from "@/components/MistakeTrend";
import ShadowDelta from "@/components/ShadowDelta";
import TimeOfDay from "@/components/TimeOfDay";
import OpenTrades from "@/components/OpenTrades";
import PendingRecommendations from "@/components/PendingRecommendations";
import ProposedTrades from "@/components/ProposedTrades";
import RiskOfRuin from "@/components/RiskOfRuin";
import SetupTypeBreakdown from "@/components/SetupTypeBreakdown";
import Stats from "@/components/Stats";
import SubNav, { type NavItem } from "@/components/SubNav";
import TopBar from "@/components/TopBar";
import WatchLevels from "@/components/WatchLevels";
import WhatIfShock from "@/components/WhatIfShock";
import {
  computeEquityCurve,
  computeHitStats,
  computeSetupTypeStats,
  currentEquity,
  currentEquityLive,
  holdTimeStats,
  liveEquityRounded,
  maeMfeAnalysis,
  maxLossStreak,
  monteCarloRuin,
  openExposure,
  portfolioHeat,
  preMortemAccuracy,
  returnSpark,
  rMultipleSpark,
  todayRealizedLoss,
  unrealizedPnl,
  winRateSpark,
} from "@/lib/compute";
import type { Portfolio } from "@/lib/types";
import { useCallback, useEffect, useMemo, useState } from "react";

const REFRESH_MS = 10_000;
const MAX_POSITIONS = 5;

export default function Dashboard({
  initial,
}: {
  initial: Portfolio;
}) {
  const [portfolio, setPortfolio] = useState<Portfolio>(initial);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [open, setOpen] = useState({
    performance: false,
    risk: false,
    insights: false,
    learning: false,
    history: false,
  });

  useEffect(() => {
    setRefreshedAt(new Date());
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const pRes = await fetch("/api/portfolio", { cache: "no-store" });
      if (pRes.ok) {
        const data = (await pRes.json()) as Portfolio;
        setPortfolio(data);
      }
      setRefreshedAt(new Date());
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [autoRefresh, refresh]);

  const equity = useMemo(() => currentEquity(portfolio), [portfolio]);
  const equityLive = useMemo(() => currentEquityLive(portfolio), [portfolio]);
  const unrealized = useMemo(() => unrealizedPnl(portfolio), [portfolio]);
  const exposure = useMemo(() => openExposure(portfolio), [portfolio]);
  // Curve key: closed trades + cash movements + total capital + rounded live
  // equity (whole €). The chart only repaints when one of these actually moves;
  // sub-€1 price ticks don't bust the cache.
  const liveEquityWhole = useMemo(() => liveEquityRounded(portfolio), [portfolio]);
  const curveKey = useMemo(() => {
    const last = portfolio.closed_trades[portfolio.closed_trades.length - 1];
    return [
      portfolio.total_capital_eur,
      portfolio.closed_trades.length,
      last?.exit_date ?? "",
      last?.pnl_eur ?? 0,
      portfolio.cash_movements?.length ?? 0,
      liveEquityWhole,
    ].join("|");
  }, [
    portfolio.total_capital_eur,
    portfolio.closed_trades,
    portfolio.cash_movements,
    liveEquityWhole,
  ]);
  const curve = useMemo(
    () => computeEquityCurve(portfolio),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [curveKey],
  );
  const stats = useMemo(
    () => computeHitStats(portfolio.closed_trades, portfolio.cash_movements ?? []),
    [portfolio.closed_trades, portfolio.cash_movements],
  );
  const setupStats = useMemo(
    () => computeSetupTypeStats(portfolio.closed_trades, portfolio.cash_movements ?? []),
    [portfolio.closed_trades, portfolio.cash_movements],
  );
  const premortem = useMemo(
    () => preMortemAccuracy(portfolio.closed_trades),
    [portfolio.closed_trades],
  );
  const holdTime = useMemo(
    () => holdTimeStats(portfolio.closed_trades),
    [portfolio.closed_trades],
  );
  const ruin = useMemo(
    () => monteCarloRuin(portfolio.closed_trades),
    [portfolio.closed_trades],
  );
  const lossStreak = useMemo(
    () => maxLossStreak(portfolio.closed_trades),
    [portfolio.closed_trades],
  );
  const maeMfe = useMemo(
    () => maeMfeAnalysis(portfolio.closed_trades),
    [portfolio.closed_trades],
  );
  const dailyLoss = useMemo(() => todayRealizedLoss(portfolio), [portfolio]);
  const heat = useMemo(() => portfolioHeat(portfolio), [portfolio]);
  const sparkReturn = useMemo(() => returnSpark(curve), [curve]);
  const sparkWin = useMemo(
    () => winRateSpark(portfolio.closed_trades),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [curveKey],
  );
  const sparkR = useMemo(
    () => rMultipleSpark(portfolio.closed_trades),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [curveKey],
  );

  const peak = curve.length ? curve[curve.length - 1].peak : equity;
  const dd = curve.length ? curve[curve.length - 1].dd_pct : 0;
  const totalReturn =
    portfolio.total_capital_eur > 0
      ? ((equityLive - portfolio.total_capital_eur) /
          portfolio.total_capital_eur) *
        100
      : 0;

  const wins = stats?.wins ?? 0;
  const losses = stats?.losses ?? 0;
  const winRate = stats?.win_rate ?? 0;
  const rMult = stats?.r_multiple ?? null;
  const cashRatio = equityLive > 0 ? portfolio.cash_eur / equityLive : 0;

  const openCount = portfolio.open_trades.length;
  const pendingCount = (portfolio.pending_recommendations ?? []).length;
  // Counter zeigt was ClosedTrades-Tabelle rendert (inkl Partials). Hit-Stats nutzen
  // separat ihren eigenen Filter für Win-Rate-Math (stats.total in Stats-Component).
  const closedCount = portfolio.closed_trades.length;

  const navItems = useMemo<NavItem[]>(
    () => [
      { id: "overview", label: "Overview" },
      { id: "active", label: "Active", count: openCount + pendingCount },
      { id: "performance", label: "Performance" },
      { id: "risk", label: "Risk" },
      { id: "insights", label: "Insights" },
      { id: "learning", label: "Learning" },
      { id: "history", label: "History", count: closedCount },
    ],
    [openCount, pendingCount, closedCount],
  );

  const jump = useCallback((id: string) => {
    if (["performance", "risk", "insights", "learning", "history"].includes(id)) {
      setOpen((o) => ({ ...o, [id]: true }));
    }
    requestAnimationFrame(() => {
      const el = document.getElementById(id);
      if (el) {
        const y = el.getBoundingClientRect().top + window.scrollY - 110;
        window.scrollTo({ top: y, behavior: "smooth" });
      }
    });
  }, []);

  // Live browser tab title: reflects open trades + unrealized PnL + drawdown
  // so the tab in the macOS dock / browser tab strip surfaces state even when
  // the page is in the background. SSR-safe: runs only after mount.
  useEffect(() => {
    const starting = Number(portfolio.total_capital_eur || 0);
    const dd = starting > 0
      ? Math.max(0, (starting - equity) / starting * 100)
      : 0;
    const unrealStr = unrealized >= 0
      ? `+€${unrealized.toFixed(2)}`
      : `-€${Math.abs(unrealized).toFixed(2)}`;
    const ddStr = dd >= 0.01 ? ` · DD ${dd.toFixed(1)}%` : "";
    const ks = portfolio.kill_switch_active || portfolio.kill_switch;
    const prefix = ks ? "🛑 " : openCount > 0 ? "📊 " : "";
    document.title = `${prefix}TA · ${openCount} open · ${unrealStr}${ddStr}`;
    return () => {
      document.title = "Trading Advisor";
    };
  }, [
    openCount, equity, unrealized,
    portfolio.total_capital_eur,
    portfolio.kill_switch_active, portfolio.kill_switch,
  ]);

  // Heartbeat staleness: seconds since last_tick. Computed client-only to avoid
  // SSR/CSR hydration mismatch (Date.now() differs between server and client).
  const [staleSecs, setStaleSecs] = useState<number | null>(null);
  useEffect(() => {
    const ts = portfolio.heartbeat?.last_tick;
    if (!ts) {
      setStaleSecs(null);
      return;
    }
    const t = Date.parse(ts.replace(" ", "T"));
    if (Number.isNaN(t)) {
      setStaleSecs(null);
      return;
    }
    const update = () =>
      setStaleSecs(Math.max(0, Math.floor((Date.now() - t) / 1000)));
    update();
    const id = setInterval(update, 30_000);
    return () => clearInterval(id);
  }, [portfolio.heartbeat?.last_tick]);

  return (
    <>
      <TopBar
        equityLive={equityLive}
        todayPct={dailyLoss.pct}
        drawdownPct={dd}
        killSwitch={!!(portfolio.kill_switch ?? portfolio.kill_switch_active)}
        killSwitchReason={portfolio.kill_switch_reason}
        ddHalt={!!portfolio.dd_halt_active}
        dailyPnlPct={dailyLoss.pct}
        heatPct={heat.pct}
        openCount={openCount}
        maxPositions={MAX_POSITIONS}
        staleSecs={staleSecs}
        refreshing={refreshing}
        onRefresh={refresh}
        refreshedLabel={
          <span suppressHydrationWarning>
            {refreshedAt ? `updated ${refreshedAt.toLocaleTimeString()}` : "—"}
          </span>
        }
      />

      <SubNav items={navItems} onJump={jump} />

      <div className="app-shell">
        {/* Overview */}
        <div id="overview" className="section section-overview">
          <div className="kgrid kgrid-4 kpi-row">
            <Kpi
              label="Total Return"
              value={`${totalReturn >= 0 ? "+" : ""}${totalReturn.toFixed(2)}%`}
              tone={
                totalReturn > 0 ? "good" : totalReturn < 0 ? "bad" : "neutral"
              }
              sub={`${equityLive.toFixed(2)} € · vs ${portfolio.total_capital_eur.toFixed(0)} € start`}
              sparkData={sparkReturn}
              sparkColor={totalReturn >= 0 ? "var(--good)" : "var(--bad)"}
            />
            <Kpi
              label="Win Rate"
              value={stats ? `${winRate.toFixed(1)}%` : "—"}
              sub={
                stats
                  ? `${wins}W / ${losses}L · ${stats.total} closed`
                  : "n<3 closed"
              }
              tone="good"
              sparkData={sparkWin}
              sparkColor="var(--good)"
            />
            <Kpi
              label="R-Multiple"
              value={rMult != null ? `${rMult.toFixed(2)}R` : "—"}
              sub={
                stats
                  ? `avg win +${stats.avg_win_pct.toFixed(1)}% · loss ${stats.avg_loss_pct.toFixed(1)}%`
                  : "n<3 closed"
              }
              tone={rMult != null && rMult >= 1 ? "good" : "neutral"}
              sparkData={sparkR}
              sparkColor="var(--good)"
            />
            <Kpi
              label="Cash / Exposure"
              value={`${(cashRatio * 100).toFixed(0)} / ${(100 - cashRatio * 100).toFixed(0)}`}
              delta={`heat ${heat.pct.toFixed(1)}%`}
              deltaPos={heat.pct < 50}
              sub={`${portfolio.cash_eur.toFixed(0)} € cash · ${exposure.toFixed(0)} € deployed${unrealized !== 0 ? ` · unreal ${unrealized >= 0 ? "+" : ""}${unrealized.toFixed(0)} €` : ""}`}
            />
          </div>

          {/* Hero equity chart */}
          <Card title="Equity Curve" sub={`peak ${peak.toFixed(2)} €`} flush>
            <div className="hero-card-pad">
              <EquityChart data={curve} />
            </div>
          </Card>
        </div>

        {/* Active */}
        <div id="active" className="section">
          <div className="section-head section-static">
            <div className="section-title">
              Active Trading State{" "}
              <span className="num">
                {openCount} open · {pendingCount} pending ·{" "}
                {portfolio.watch_levels.length} watch
              </span>
            </div>
            <div className="section-meta hide-mobile">live state</div>
          </div>
          <div className="active-grid">
            <div className="stack">
              <Card title="Open Trades" badge={`${openCount}`}>
                <OpenTrades
                  trades={portfolio.open_trades}
                  livePrices={portfolio.heartbeat?.prices}
                />
              </Card>
              <Card
                title="Watch Levels"
                badge={`${portfolio.watch_levels.length}`}
              >
                <WatchLevels
                  levels={portfolio.watch_levels}
                  liveQuotes={portfolio.heartbeat?.live_quotes}
                />
              </Card>
            </div>
            <div className="stack">
              <Card
                title="Trade-Vorschläge"
                badge={`${portfolio.proposed_trades?.length ?? 0}`}
              >
                <ProposedTrades proposals={portfolio.proposed_trades ?? []} />
              </Card>
              <Card title="Pending Recommendations" badge={`${pendingCount}`}>
                <PendingRecommendations
                  recs={portfolio.pending_recommendations}
                />
              </Card>
              <Card title="Heartbeat">
                <Heartbeat
                  heartbeat={portfolio.heartbeat}
                  killSwitchActive={
                    portfolio.kill_switch ?? portfolio.kill_switch_active
                  }
                  killSwitchReason={portfolio.kill_switch_reason}
                  killSwitchTs={portfolio.kill_switch_ts}
                  ddHaltActive={portfolio.dd_halt_active}
                />
              </Card>
            </div>
          </div>
        </div>

        {/* Performance */}
        <CollapsibleSection
          id="performance"
          title="Performance & Calibration"
          meta={
            stats
              ? `win-rate ${winRate.toFixed(0)}% · R ${rMult != null ? rMult.toFixed(2) : "—"}${stats.calibration ? ` · Brier ${stats.calibration.avg_brier.toFixed(2)}` : ""}`
              : ""
          }
          open={open.performance}
          onToggle={() =>
            setOpen((o) => ({ ...o, performance: !o.performance }))
          }
        >
          <Card title="Hit Stats">
            <Stats stats={stats} premortem={premortem} holdTime={holdTime} />
          </Card>
          <Card title="Calibration Curve · p_win vs reality">
            <CalibrationCurve />
          </Card>
          <Card title="Setup-Type Performance">
            <SetupTypeBreakdown stats={setupStats} />
          </Card>
          <Card title="MAE/MFE — SL/TP-Tuning Insight (R-multiples)">
            <MaeMfe stats={maeMfe} />
          </Card>
        </CollapsibleSection>

        {/* Risk */}
        <CollapsibleSection
          id="risk"
          title="Risk"
          meta={`heat ${heat.pct.toFixed(1)}%${ruin ? ` · P(ruin≥25%) ${ruin.ruin_prob_pct.toFixed(1)}%` : ""} · max DD ${dd.toFixed(2)}%`}
          open={open.risk}
          onToggle={() => setOpen((o) => ({ ...o, risk: !o.risk }))}
        >
          <Card title="Risk-of-Ruin · Loss-Streak (Monte Carlo)">
            <RiskOfRuin ruin={ruin} lossStreak={lossStreak} />
          </Card>
          <Card title="What-If Shock">
            <WhatIfShock open={portfolio.open_trades} />
          </Card>
          <Card title="Correlation Heatmap (open positions)">
            <CorrelationHeatmap
              matrix={portfolio.correlation_matrix}
              openTickers={portfolio.open_trades.map((t) => t.ticker)}
            />
          </Card>
        </CollapsibleSection>

        {/* Insights — analytics-driven views (Phase E follow-up) */}
        <CollapsibleSection
          id="insights"
          title="Insights"
          meta="bot-computed analytics"
          open={open.insights}
          onToggle={() => setOpen((o) => ({ ...o, insights: !o.insights }))}
        >
          <Card title="Drawdown Trajectory">
            <DrawdownTrajectory />
          </Card>
          <Card title="Hit-Rate Trend (rolling 30d)">
            <HitRateTrend />
          </Card>
          <Card title="Time-of-Day Expectancy">
            <TimeOfDay />
          </Card>
          <Card title="Earnings Calendar (next 14d)">
            <EarningsCalendar />
          </Card>
          <Card title="Shadow Config — What-If">
            <ShadowDelta />
          </Card>
        </CollapsibleSection>

        {/* Learning */}
        <CollapsibleSection
          id="learning"
          title="Learning"
          meta={`${losses} losses tagged`}
          open={open.learning}
          onToggle={() => setOpen((o) => ({ ...o, learning: !o.learning }))}
        >
          <Card title="Mistake-Class Trend (rolling 10 losses)">
            <MistakeTrend closed={portfolio.closed_trades} />
          </Card>
          <Card title="Gate Attribution">
            <GateAttribution />
          </Card>
          <Card title="Bot Activity (chronological)">
            <BotActivityTimeline />
          </Card>
        </CollapsibleSection>

        {/* History */}
        <CollapsibleSection
          id="history"
          title="History"
          meta={
            stats?.total_pnl_eur != null
              ? `${closedCount} closed · realized ${stats.total_pnl_eur >= 0 ? "+" : ""}${stats.total_pnl_eur.toFixed(2)} €`
              : `${closedCount} closed`
          }
          open={open.history}
          onToggle={() => setOpen((o) => ({ ...o, history: !o.history }))}
        >
          <Card title="Closed Trades (recent 25)">
            <ClosedTrades
              trades={portfolio.closed_trades}
              movements={portfolio.cash_movements ?? []}
            />
          </Card>
          <Card title="Backtest Replay (gates × closed trades)">
            <BacktestReport />
          </Card>
        </CollapsibleSection>

        <footer className="app-footer">
          <span>
            trading-advisor · last update {portfolio.last_updated ?? "—"} · last
            analysis {portfolio.last_analysis ?? "—"}
          </span>
          <label>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="accent-emerald-500"
            />
            auto-refresh 10s
          </label>
        </footer>
      </div>
    </>
  );
}
