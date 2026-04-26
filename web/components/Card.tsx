import type { ReactNode } from "react";

export function Card({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 ${className}`}
    >
      {title && (
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-400">
          {title}
        </h2>
      )}
      {children}
    </section>
  );
}

export function Kpi({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "neutral" | "good" | "bad" | "warn";
}) {
  const toneCls =
    tone === "good"
      ? "text-emerald-400"
      : tone === "bad"
        ? "text-rose-400"
        : tone === "warn"
          ? "text-amber-400"
          : "text-zinc-100";
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="text-xs uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-semibold ${toneCls}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}
