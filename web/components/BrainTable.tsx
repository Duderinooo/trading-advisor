"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import type { ClaudeCall } from "@/lib/types";

const MODE_TONE: Record<string, string> = {
  morning: "bg-purple-900/40 text-purple-300 border-purple-800",
  opening: "bg-blue-900/40 text-blue-300 border-blue-800",
  event: "bg-emerald-900/40 text-emerald-300 border-emerald-800",
  red_team: "bg-rose-900/40 text-rose-300 border-rose-800",
};

function fmtTs(ts: string): string {
  const m = ts.match(/(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  return m ? `${m[1]} ${m[2]}:${m[3]}` : ts;
}

function tokenSummary(c: ClaudeCall): string {
  const t = c.tokens;
  const parts: string[] = [];
  if (t.input != null) parts.push(`in ${t.input}`);
  if (t.output != null) parts.push(`out ${t.output}`);
  if (t.cache_read) parts.push(`cache-read ${t.cache_read}`);
  if (t.cache_write) parts.push(`cache-write ${t.cache_write}`);
  return parts.join(" · ");
}

function CallDetail({ call }: { call: ClaudeCall }) {
  const [systemBody, setSystemBody] = useState<string | null>(null);
  const [showSystem, setShowSystem] = useState(false);

  const fetchSystem = async () => {
    if (systemBody !== null) {
      setShowSystem((s) => !s);
      return;
    }
    try {
      const res = await fetch(
        `/api/system-prompt?hash=${encodeURIComponent(call.system_hash)}`,
      );
      if (res.ok) {
        const data = (await res.json()) as { body?: string };
        setSystemBody(data.body ?? "(empty)");
        setShowSystem(true);
      } else {
        setSystemBody("(prompt not found in .system_prompts/)");
        setShowSystem(true);
      }
    } catch {
      setSystemBody("(fetch failed)");
      setShowSystem(true);
    }
  };

  return (
    <div className="space-y-3 text-xs">
      <div>
        <button
          type="button"
          onClick={fetchSystem}
          className="text-zinc-500 hover:text-zinc-200"
        >
          {showSystem ? "▾" : "▸"} System Prompt ({call.system_hash})
        </button>
        {showSystem && systemBody !== null && (
          <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded border border-zinc-800 bg-zinc-950/60 p-3 text-[11px] text-zinc-400">
            {systemBody}
          </pre>
        )}
      </div>

      <div>
        <div className="text-zinc-500 mb-1">User Message</div>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-zinc-800 bg-zinc-950/60 p-3 text-[11px] text-zinc-300">
          {call.user_message || "(empty)"}
        </pre>
      </div>

      {call.text_response && (
        <div>
          <div className="text-zinc-500 mb-1">Text Response</div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-zinc-800 bg-zinc-950/60 p-3 text-[11px] text-emerald-300">
            {call.text_response}
          </pre>
        </div>
      )}

      {call.tool_calls.length > 0 && (
        <div>
          <div className="text-zinc-500 mb-1">
            Tool Calls ({call.tool_calls.length})
          </div>
          <div className="space-y-2">
            {call.tool_calls.map((tc, i) => (
              <div
                key={i}
                className="rounded border border-cyan-900 bg-cyan-950/30 p-3"
              >
                <div className="font-mono text-cyan-300 mb-1">{tc.name}</div>
                <pre className="whitespace-pre-wrap text-[11px] text-zinc-300">
                  {JSON.stringify(tc.input, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        </div>
      )}

      {call.extra && Object.keys(call.extra).length > 0 && (
        <div>
          <div className="text-zinc-500 mb-1">Extra</div>
          <pre className="text-[11px] text-zinc-400">
            {JSON.stringify(call.extra, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

export default function BrainTable() {
  const [calls, setCalls] = useState<ClaudeCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [modeFilter, setModeFilter] = useState<string>("all");
  const [openRows, setOpenRows] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/claude-calls?limit=200", {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as { calls: ClaudeCall[] };
        if (!cancelled) setCalls(data.calls ?? []);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const modes = useMemo(() => {
    const set = new Set<string>();
    for (const c of calls) set.add(c.mode);
    return ["all", ...Array.from(set).sort()];
  }, [calls]);

  const filtered = useMemo(() => {
    const list = modeFilter === "all" ? calls : calls.filter((c) => c.mode === modeFilter);
    return [...list].reverse();
  }, [calls, modeFilter]);

  if (loading) {
    return <div className="text-sm text-zinc-500">Lade Bot-Calls…</div>;
  }
  if (calls.length === 0) {
    return (
      <div className="text-sm text-zinc-500">
        Noch keine Calls in claude_calls.jsonl. Bot muss erst einen Call machen.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-zinc-500">Mode:</span>
        {modes.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setModeFilter(m)}
            className={`px-2 py-1 rounded border ${
              modeFilter === m
                ? "border-zinc-500 bg-zinc-800 text-zinc-100"
                : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {m}
          </button>
        ))}
        <span className="ml-auto text-zinc-500">
          {filtered.length} call(s)
        </span>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
            <th className="py-2 pr-3"></th>
            <th className="py-2 pr-3">When</th>
            <th className="py-2 pr-3">Mode</th>
            <th className="py-2 pr-3">Model</th>
            <th className="py-2 pr-3">Turn</th>
            <th className="py-2 pr-3">Tokens</th>
            <th className="py-2 pr-3">Tool-Calls</th>
            <th className="py-2 pr-3">Text-Snippet</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((c, i) => {
            const key = `${c.ts}-${c.turn}-${i}`;
            const isOpen = openRows[key] ?? false;
            const tone =
              MODE_TONE[c.mode] ??
              "bg-zinc-900/60 text-zinc-300 border-zinc-800";
            return (
              <Fragment key={key}>
                <tr
                  className="border-b border-zinc-900/60 hover:bg-zinc-900/30 cursor-pointer"
                  onClick={() =>
                    setOpenRows((s) => ({ ...s, [key]: !s[key] }))
                  }
                >
                  <td className="py-2 pr-2 text-zinc-500">{isOpen ? "▾" : "▸"}</td>
                  <td className="py-2 pr-3 font-mono text-zinc-400">
                    {fmtTs(c.ts)}
                  </td>
                  <td className="py-2 pr-3">
                    <span className={`text-xs px-2 py-0.5 rounded border ${tone}`}>
                      {c.mode}
                    </span>
                  </td>
                  <td className="py-2 pr-3 font-mono text-zinc-500">
                    {c.model.replace("claude-", "")}
                  </td>
                  <td className="py-2 pr-3 text-zinc-400">{c.turn}</td>
                  <td className="py-2 pr-3 text-zinc-500 font-mono text-xs">
                    {tokenSummary(c)}
                  </td>
                  <td className="py-2 pr-3 text-cyan-400">
                    {c.tool_calls.length > 0
                      ? c.tool_calls.map((t) => t.name).join(", ")
                      : "—"}
                  </td>
                  <td className="py-2 pr-3 text-zinc-300 max-w-md truncate">
                    {(c.text_response || "(no text)").split("\n")[0]}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="border-b border-zinc-900/60 bg-zinc-950/40">
                    <td colSpan={8} className="py-3 px-4">
                      <CallDetail call={c} />
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
