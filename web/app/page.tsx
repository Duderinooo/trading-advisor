import { readPortfolio } from "@/lib/portfolio";
import Dashboard from "@/components/Dashboard";

export const dynamic = "force-dynamic";

export default async function Home() {
  const initial = await readPortfolio();
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <Dashboard initial={initial} />
    </div>
  );
}
