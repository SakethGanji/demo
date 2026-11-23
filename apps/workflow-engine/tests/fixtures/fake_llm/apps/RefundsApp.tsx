import { useEffect, useState } from "react";
import { api } from "./api/client";

export default function App() {
  const [stats, setStats] = useState<{ count: number; total: number } | null>(null);
  useEffect(() => {
    api.refundStats().then(setStats);
  }, []);
  return (
    <main>
      <h1>Refunds</h1>
      <p data-testid="refund-count">{stats ? stats.count : "…"}</p>
      <p data-testid="refund-total">{stats ? stats.total : "…"}</p>
    </main>
  );
}
