import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { fetchJSON } from "@/lib/api";
import { useSaferRealtime } from "@/lib/ws";
import HeartbeatStrip from "./HeartbeatStrip";

interface CostSummary {
  total_usd: number;
  today_usd: number;
  total_calls: number;
  by_component: Record<string, number>;
}

export default function TopBar() {
  const { connected } = useSaferRealtime();
  const [cost, setCost] = useState<CostSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchJSON<CostSummary>("/v1/stats/cost");
        if (!cancelled) setCost(data);
      } catch {
        /* backend may still be starting */
      }
    };
    load();
    const t = window.setInterval(load, 10_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  const budget = 500; // USD (hackathon)
  const remaining = Math.max(0, budget - (cost?.total_usd ?? 0));
  const pct = Math.min(100, Math.round(((cost?.total_usd ?? 0) / budget) * 100));

  return (
    <div className="h-14 border-b border-border flex items-center justify-between px-6 bg-background/70 backdrop-blur sticky top-0 z-10">
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <div className="flex items-center gap-2 text-sm">
          <span
            className={cn(
              "h-2 w-2 rounded-full",
              connected
                ? "bg-safer-success animate-pulse"
                : "bg-muted-foreground"
            )}
          />
          <span className="text-muted-foreground text-xs font-mono">
            {connected ? "stream live" : "stream offline"}
          </span>
        </div>
        <div className="flex-1 max-w-md hidden md:block">
          <HeartbeatStrip />
        </div>
      </div>
      <div className="flex items-center gap-6">
        <div className="text-right leading-tight">
          <div className="text-xs text-muted-foreground">spent today</div>
          <div className="font-mono text-sm">
            ${(cost?.today_usd ?? 0).toFixed(2)}
          </div>
        </div>
        <div className="text-right leading-tight">
          <div className="text-xs text-muted-foreground">credit remaining</div>
          <div
            className={cn(
              "font-mono text-sm",
              pct > 80 ? "text-safer-critical" : pct > 60 ? "text-safer-warning" : "text-safer-success"
            )}
          >
            ${remaining.toFixed(2)}
          </div>
        </div>
      </div>
    </div>
  );
}
