import Dashboard from "@/components/Dashboard";
import { readPaperPortfolio, readPortfolio } from "@/lib/portfolio";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [initial, paper] = await Promise.all([
    readPortfolio(),
    readPaperPortfolio(),
  ]);
  const initialPaper = paper ?? {
    paper: false as const,
    open_trades: [] as [],
    closed_trades: [] as [],
    cash_eur: 0,
    total_capital_eur: 0,
  };
  return <Dashboard initial={initial} initialPaper={initialPaper} />;
}
