import { readPortfolio } from "@/lib/portfolio";
import Dashboard from "@/components/Dashboard";

export const dynamic = "force-dynamic";

export default async function Home() {
  const initial = await readPortfolio();
  return <Dashboard initial={initial} />;
}
