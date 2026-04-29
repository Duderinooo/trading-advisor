import type { AnalyzeTrace, Portfolio } from "@/lib/types";

type Props = {
  portfolio: Portfolio;
};

const MODE_LABEL: Record<string, string> = {
  morning: "Morning",
  event: "Event",
  opening_xetra: "XETRA Open",
  opening_us: "US Open",
};

function tracesFromPortfolio(p: Portfolio): { label: string; trace: AnalyzeTrace }[] {
  const out: { label: string; trace: AnalyzeTrace }[] = [];
  if (p.last_morning_trace) out.push({ label: MODE_LABEL.morning, trace: p.last_morning_trace });
  if (p.last_opening_trace_xetra)
    out.push({ label: MODE_LABEL.opening_xetra, trace: p.last_opening_trace_xetra });
  if (p.last_opening_trace_us)
    out.push({ label: MODE_LABEL.opening_us, trace: p.last_opening_trace_us });
  if (p.last_event_trace) out.push({ label: MODE_LABEL.event, trace: p.last_event_trace });
  out.sort((a, b) => (b.trace.ts || "").localeCompare(a.trace.ts || ""));
  return out;
}

function ageMin(ts: string): number | null {
  const m = ts.match(/(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})/);
  if (!m) return null;
  const t = Date.parse(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}`);
  if (Number.isNaN(t)) return null;
  return Math.round((Date.now() - t) / 60000);
}

function TraceRow({ label, trace }: { label: string; trace: AnalyzeTrace }) {
  const failure =
    !trace.tool_called && trace.mode === "morning"
      ? "TOOL_NOT_CALLED"
      : trace.malformed_tool_input
        ? "MALFORMED_INPUT"
        : trace.truncated
          ? "TRUNCATED"
          : null;
  const age = ageMin(trace.ts);

  return (
    <div className="border border-zinc-800 rounded p-3 bg-zinc-950 text-xs space-y-2">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-zinc-200">{label}</span>
          <span className="text-zinc-500">{trace.ts}</span>
          {age !== null && (
            <span className="text-zinc-600">
              ({age < 60 ? `${age}min` : `${Math.round(age / 60)}h`} ago)
            </span>
          )}
        </div>
        {failure && (
          <span className="px-2 py-0.5 rounded border border-rose-800 bg-rose-900/40 text-rose-300 font-mono">
            {failure}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-zinc-400">
        <div>
          <div className="text-zinc-500">tool_called</div>
          <div className={trace.tool_called ? "text-emerald-300" : "text-rose-300"}>
            {String(trace.tool_called)}
          </div>
        </div>
        <div>
          <div className="text-zinc-500">raw / final</div>
          <div className="text-zinc-200">
            {trace.raw_levels_count}
            {" / "}
            {trace.final_count ?? "—"}
          </div>
        </div>
        <div>
          <div className="text-zinc-500">stop_reason</div>
          <div className={trace.truncated ? "text-rose-300" : "text-zinc-200"}>
            {trace.stop_reason ?? "—"}
          </div>
        </div>
        <div>
          <div className="text-zinc-500">tokens</div>
          <div className="text-zinc-200">
            {trace.output_tokens ?? "?"}/{trace.max_tokens_budget}
          </div>
        </div>
      </div>

      {(trace.dropped_excluded || trace.dropped_self_sabotage) && (
        <div className="text-zinc-500">
          {trace.dropped_excluded ? `dropped_excluded=${trace.dropped_excluded} ` : ""}
          {trace.dropped_self_sabotage
            ? `dropped_self_sabotage=${trace.dropped_self_sabotage}`
            : ""}
        </div>
      )}

      {trace.final_tickers && trace.final_tickers.length > 0 && (
        <div className="text-zinc-400">
          <span className="text-zinc-500">final tickers: </span>
          <span className="font-mono">{trace.final_tickers.join(", ")}</span>
        </div>
      )}

      {trace.sonnet_text && (
        <pre className="text-zinc-400 whitespace-pre-wrap break-words bg-zinc-900 p-2 rounded border border-zinc-800 font-mono">
          {trace.sonnet_text}
        </pre>
      )}
    </div>
  );
}

export default function AnalyzeTraceCard({ portfolio }: Props) {
  const traces = tracesFromPortfolio(portfolio);
  if (traces.length === 0) {
    return (
      <section className="mb-6 border border-zinc-800 rounded p-4 bg-zinc-950">
        <h2 className="text-sm font-semibold text-zinc-300 mb-2">Analyze Traces</h2>
        <p className="text-xs text-zinc-500">
          Noch keine Traces in portfolio.json. Wird beim ersten morning/opening/event
          Sonnet/Haiku-Call befüllt.
        </p>
      </section>
    );
  }
  return (
    <section className="mb-6 space-y-3">
      <h2 className="text-sm font-semibold text-zinc-300">
        Analyze Traces
        <span className="ml-2 text-xs font-normal text-zinc-500">
          (last call per mode — pipeline visibility for set_watch_levels failures)
        </span>
      </h2>
      <div className="space-y-2">
        {traces.map((t) => (
          <TraceRow key={t.label} label={t.label} trace={t.trace} />
        ))}
      </div>
    </section>
  );
}
