import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ShieldAlert, Eye, Ban } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { BACKEND_URL, fetchJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

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

type GuardMode = "monitor" | "intervene" | "enforce";

interface ConfigSnapshot {
  guard_mode: GuardMode;
  valid_guard_modes: GuardMode[];
}

const GUARD_MODES: Array<{
  value: GuardMode;
  label: string;
  blurb: string;
  icon: typeof Eye;
}> = [
  {
    value: "monitor",
    label: "Monitor",
    blurb: "Log only. Never blocks. Safe to turn on first.",
    icon: Eye,
  },
  {
    value: "intervene",
    label: "Intervene",
    blurb: "Block CRITICAL hits and a short list of high-impact flags.",
    icon: ShieldAlert,
  },
  {
    value: "enforce",
    label: "Enforce",
    blurb: "Block every HIGH-or-worse policy hit.",
    icon: Ban,
  },
];

export default function Settings() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [cost, setCost] = useState<CostSummary | null>(null);
  const [config, setConfig] = useState<ConfigSnapshot | null>(null);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    try {
      const [s, c, cfg] = await Promise.all([
        fetchJSON<Stats>("/v1/stats"),
        fetchJSON<CostSummary>("/v1/stats/cost"),
        fetchJSON<ConfigSnapshot>("/v1/config"),
      ]);
      setStats(s);
      setCost(c);
      setConfig(cfg);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    loadAll();
    const t = window.setInterval(loadAll, 10_000);
    return () => clearInterval(t);
  }, [loadAll]);

  const setGuardMode = async (mode: GuardMode) => {
    if (!config || config.guard_mode === mode) return;
    setSaving(true);
    setError(null);
    try {
      const r = await fetch(`${BACKEND_URL}/v1/config`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guard_mode: mode }),
      });
      if (!r.ok) {
        throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      }
      const snap = (await r.json()) as ConfigSnapshot;
      setConfig(snap);
      setFlash(`Guard mode → ${snap.guard_mode}`);
      window.setTimeout(() => setFlash(null), 2500);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          System status, cost, and runtime configuration.
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between">
            <div>
              <CardTitle className="text-base">Guard mode</CardTitle>
              <p className="text-xs text-muted-foreground font-mono">
                Controls how the Gateway reacts when a policy hits at runtime.
                Changes apply immediately to every new event; the SDK learns
                about them on the next call.
              </p>
            </div>
            {flash && (
              <div className="flex items-center gap-1 text-xs text-safer-success font-mono">
                <CheckCircle2 className="h-4 w-4" />
                {flash}
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {config === null && !error && (
            <p className="text-xs text-muted-foreground font-mono">loading…</p>
          )}
          {error && (
            <div className="flex items-start gap-2 text-xs text-safer-critical font-mono">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="break-all">{error}</span>
            </div>
          )}
          {config && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              {GUARD_MODES.map((m) => {
                const active = config.guard_mode === m.value;
                const Icon = m.icon;
                return (
                  <button
                    key={m.value}
                    onClick={() => setGuardMode(m.value)}
                    disabled={saving || active}
                    className={cn(
                      "text-left rounded-md border p-3 transition",
                      active
                        ? "border-safer-ice/60 bg-safer-ice/10 cursor-default"
                        : "border-border hover:bg-muted/40 cursor-pointer"
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Icon
                        className={cn(
                          "h-4 w-4",
                          active ? "text-safer-ice" : "text-muted-foreground"
                        )}
                      />
                      <span className="text-sm font-medium font-mono">
                        {m.label}
                      </span>
                      {active && (
                        <Badge variant="ice" className="ml-auto">
                          active
                        </Badge>
                      )}
                    </div>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {m.blurb}
                    </p>
                  </button>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Backend status</CardTitle>
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
          <CardTitle className="text-base">Claude cost</CardTitle>
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
    </div>
  );
}
