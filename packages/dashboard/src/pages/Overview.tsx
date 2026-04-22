import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { fetchJSON } from "@/lib/api";
import { useSaferRealtime } from "@/lib/ws";
import { Bot, ListTree, Activity, DollarSign } from "lucide-react";

interface Stats {
  agents: number;
  sessions: number;
  active_sessions: number;
  events: number;
}

interface CostSummary {
  total_usd: number;
  today_usd: number;
  total_calls: number;
  by_component: Record<string, number>;
}

export default function Overview() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [cost, setCost] = useState<CostSummary | null>(null);
  const { events } = useSaferRealtime(20);

  useEffect(() => {
    const load = async () => {
      try {
        const [s, c] = await Promise.all([
          fetchJSON<Stats>("/v1/stats"),
          fetchJSON<CostSummary>("/v1/stats/cost"),
        ]);
        setStats(s);
        setCost(c);
      } catch {
        /* empty */
      }
    };
    load();
    const t = window.setInterval(load, 5_000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
          <p className="text-sm text-muted-foreground">
            Live agent control plane health.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard label="Agents" value={stats?.agents ?? "—"} icon={Bot} />
        <KpiCard
          label="Sessions"
          value={stats?.sessions ?? "—"}
          sublabel={`${stats?.active_sessions ?? 0} active`}
          icon={ListTree}
        />
        <KpiCard label="Events" value={stats?.events ?? "—"} icon={Activity} />
        <KpiCard
          label="Spent today"
          value={`$${(cost?.today_usd ?? 0).toFixed(2)}`}
          sublabel={`${cost?.total_calls ?? 0} calls`}
          icon={DollarSign}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent activity</CardTitle>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <p className="text-sm text-muted-foreground font-mono">
              No events yet. Run an instrumented agent to see activity here.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {[...events].reverse().map((ev) => (
                <li
                  key={ev.event_id}
                  className="py-2 flex items-center gap-3 text-sm font-mono"
                >
                  <span className="text-muted-foreground shrink-0 w-20 text-xs">
                    {new Date(ev.timestamp).toLocaleTimeString()}
                  </span>
                  <Badge variant="outline" className="shrink-0 font-normal">
                    {ev.hook}
                  </Badge>
                  <span className="text-muted-foreground shrink-0">
                    {ev.agent_id}
                  </span>
                  <span className="text-muted-foreground truncate">
                    #{ev.sequence}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function KpiCard({
  label,
  value,
  sublabel,
  icon: Icon,
}: {
  label: string;
  value: React.ReactNode;
  sublabel?: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start justify-between">
          <div>
            <div className="text-sm text-muted-foreground">{label}</div>
            <div className="text-2xl font-semibold tabular-nums mt-1">{value}</div>
            {sublabel && (
              <div className="text-xs text-muted-foreground mt-1">{sublabel}</div>
            )}
          </div>
          <Icon className="h-5 w-5 text-muted-foreground" />
        </div>
      </CardContent>
    </Card>
  );
}
