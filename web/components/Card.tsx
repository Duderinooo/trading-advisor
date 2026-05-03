import type { ReactNode } from "react";
import Sparkline from "./Sparkline";

export function Card({
  title,
  badge,
  sub,
  flush,
  children,
  className = "",
}: {
  title?: string;
  badge?: string;
  sub?: ReactNode;
  flush?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title && (
        <div className="card-head">
          <div className="card-title">
            {title}
            {badge && <span className="card-badge">{badge}</span>}
          </div>
          {sub && <div className="card-sub">{sub}</div>}
        </div>
      )}
      <div className={`card-body ${flush ? "flush" : ""}`}>{children}</div>
    </section>
  );
}

export function Kpi({
  label,
  value,
  sub,
  delta,
  deltaPos,
  tone = "neutral",
  sparkData,
  sparkColor,
}: {
  label: string;
  value: string;
  sub?: string;
  delta?: string;
  deltaPos?: boolean;
  tone?: "neutral" | "good" | "bad" | "warn";
  sparkData?: { v: number }[];
  sparkColor?: string;
}) {
  const valCls =
    tone === "good"
      ? "num-pos"
      : tone === "bad"
        ? "num-neg"
        : tone === "warn"
          ? "num-warn"
          : "";
  return (
    <div className="kpi">
      <div className="kpi-label">
        <span>{label}</span>
        {delta != null && (
          <span className={`delta ${deltaPos ? "num-pos" : "num-neg"}`}>
            {delta}
          </span>
        )}
      </div>
      <div className={`kpi-value ${valCls}`}>{value}</div>
      <div className="kpi-foot">
        {sub && <div className="kpi-meta">{sub}</div>}
        {sparkData && sparkData.length >= 2 && (
          <div className="kpi-spark">
            <Sparkline
              data={sparkData}
              color={sparkColor ?? "var(--good)"}
              width={70}
              height={24}
            />
          </div>
        )}
      </div>
    </div>
  );
}
