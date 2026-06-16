import Dashboard from "@/components/Dashboard";
import { readPortfolio } from "@/lib/portfolio";

export const dynamic = "force-dynamic";

export default async function Home() {
  const initial = await readPortfolio();
  return <Dashboard initial={initial} />;
}
