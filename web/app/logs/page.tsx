import LogsViewer from "@/components/LogsViewer";

export const dynamic = "force-dynamic";

export default function LogsPage() {
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Logs</h1>
          <p className="text-xs text-zinc-500">
            Tail von bot.log und bot.err — live aus Working Dir
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
            href="/learning"
            className="text-xs px-3 py-1.5 rounded border border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
          >
            Learning →
          </a>
        </div>
      </header>
      <main className="p-6 max-w-7xl mx-auto">
        <LogsViewer />
      </main>
    </div>
  );
}
