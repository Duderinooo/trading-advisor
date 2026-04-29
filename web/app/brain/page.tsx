import BrainTable from "@/components/BrainTable";

export const dynamic = "force-dynamic";

export default function BrainPage() {
  return (
    <>
      <header className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-lg font-semibold">Brain · Claude-Calls</h1>
          <p className="text-xs text-zinc-500">
            Jeder API-Call mit User-Message, Text-Response, Tool-Calls. Klick
            auf Zeile für Details.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <a
            href="/"
            className="text-xs px-3 py-1.5 rounded border border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          >
            ← Dashboard
          </a>
          <a
            href="/logs"
            className="text-xs px-3 py-1.5 rounded border border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          >
            Logs →
          </a>
        </div>
      </header>
      <main className="p-6 max-w-7xl mx-auto">
        <BrainTable />
      </main>
    </>
  );
}
