"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type LogResp = {
  file: string;
  size: number;
  mtime: string;
  lines: number;
  tail: string;
};

const FILES = ["bot.log", "bot.err"] as const;
const LINE_OPTS = [100, 300, 1000];

export default function LogsViewer() {
  const [file, setFile] = useState<(typeof FILES)[number]>("bot.log");
  const [lineCount, setLineCount] = useState(300);
  const [data, setData] = useState<LogResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [autoTail, setAutoTail] = useState(true);
  const [follow, setFollow] = useState(true);
  const preRef = useRef<HTMLPreElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/logs?file=${file}&lines=${lineCount}`,
        { cache: "no-store" },
      );
      if (!res.ok) {
        setErr(`HTTP ${res.status}`);
        return;
      }
      const j = (await res.json()) as LogResp & { error?: string };
      if (j.error) setErr(j.error);
      else {
        setData(j);
        setErr(null);
      }
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [file, lineCount]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!autoTail) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [autoTail, load]);

  useEffect(() => {
    if (follow && preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [data, follow]);

  const isErr = file === "bot.err";

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex gap-1">
          {FILES.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFile(f)}
              className={`px-3 py-1.5 text-xs rounded border transition-colors ${
                file === f
                  ? "bg-zinc-100 text-zinc-900 border-zinc-100"
                  : "bg-zinc-900 text-zinc-400 border-zinc-800 hover:border-zinc-700"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          {LINE_OPTS.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => setLineCount(n)}
              className={`px-2 py-1 text-xs rounded border ${
                lineCount === n
                  ? "bg-zinc-100 text-zinc-900 border-zinc-100"
                  : "bg-zinc-900 text-zinc-500 border-zinc-800 hover:border-zinc-700"
              }`}
            >
              {n}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-xs text-zinc-500 cursor-pointer">
          <input
            type="checkbox"
            checked={autoTail}
            onChange={(e) => setAutoTail(e.target.checked)}
            className="accent-emerald-500"
          />
          Auto 5s
        </label>
        <label className="flex items-center gap-2 text-xs text-zinc-500 cursor-pointer">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
            className="accent-emerald-500"
          />
          Follow
        </label>
        <button
          type="button"
          onClick={load}
          className="text-xs px-3 py-1.5 rounded border border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-700"
        >
          ↻
        </button>
        {data && (
          <span className="text-xs text-zinc-500 ml-auto">
            {data.lines} lines · {(data.size / 1024).toFixed(1)} KB · mtime{" "}
            {new Date(data.mtime).toLocaleTimeString()}
          </span>
        )}
      </div>

      {err && (
        <div className="text-sm text-rose-400 border border-rose-800 bg-rose-900/20 rounded p-2">
          {err}
        </div>
      )}

      <pre
        ref={preRef}
        className={`h-[70vh] overflow-auto rounded-lg border border-zinc-800 bg-black p-3 text-xs font-mono whitespace-pre-wrap ${
          isErr ? "text-rose-300" : "text-zinc-300"
        }`}
      >
        {data?.tail ?? "Lade…"}
      </pre>
    </div>
  );
}
