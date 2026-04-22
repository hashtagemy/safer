import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { fetchJSON } from "@/lib/api";

interface Stats {
  agents: number;
  sessions: number;
  events: number;
  active_sessions: number;
}

interface CostSummary {
  total_usd: number;
  today_usd: number;
  total_calls: number;
  by_component: Record<string, number>;
}

export default function Settings() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [cost, setCost] = useState<CostSummary | null>(null);

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
  }, []);

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          System status and configuration.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Backend status</CardTitle>
        </CardHeader>
        <CardContent>
          {stats ? (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm font-mono">
              <dt className="text-muted-foreground">agents</dt>
              <dd>{stats.agents}</dd>
              <dt className="text-muted-foreground">sessions</dt>
              <dd>
                {stats.sessions}{" "}
                <span className="text-muted-foreground text-xs">
                  ({stats.active_sessions} active)
                </span>
              </dd>
              <dt className="text-muted-foreground">events</dt>
              <dd>{stats.events}</dd>
            </dl>
          ) : (
            <p className="text-sm text-muted-foreground font-mono">
              backend unreachable
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Claude cost</CardTitle>
        </CardHeader>
        <CardContent>
          {cost ? (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm font-mono">
              <dt className="text-muted-foreground">spent today</dt>
              <dd>${cost.today_usd.toFixed(4)}</dd>
              <dt className="text-muted-foreground">total spent</dt>
              <dd>${cost.total_usd.toFixed(4)}</dd>
              <dt className="text-muted-foreground">total calls</dt>
              <dd>{cost.total_calls}</dd>
              {Object.entries(cost.by_component).map(([k, v]) => (
                <div key={k} className="col-span-2 flex justify-between text-xs">
                  <span className="text-muted-foreground">{k}</span>
                  <span>${v.toFixed(4)}</span>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-sm text-muted-foreground font-mono">
              no cost data yet
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Guard mode</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground font-mono">
            Configure via <code>SAFER_GUARD_MODE</code> env var on the SDK side
            (monitor | intervene | enforce). Per-agent override comes in Phase
            7.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
