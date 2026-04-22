import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { fetchJSON } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { SessionListItem } from "@/lib/sessionTypes";

interface ListResponse {
  sessions: SessionListItem[];
}

function fmtDuration(startIso: string, endIso: string | null): string {
  if (!endIso) return "running";
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`;
}

function healthTone(v: number | null): string {
  if (v === null) return "text-muted-foreground";
  if (v >= 90) return "text-safer-success";
  if (v >= 70) return "text-safer-ice";
  if (v >= 40) return "text-safer-warning";
  return "text-safer-critical";
}

export default function Sessions() {
  const [rows, setRows] = useState<SessionListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState("");

  const load = useCallback(async (agent: string) => {
    setError(null);
    try {
      const qs = agent ? `?agent_id=${encodeURIComponent(agent)}` : "";
      const r = await fetchJSON<ListResponse>(`/v1/sessions${qs}`);
      setRows(r.sessions);
    } catch (e) {
      setError((e as Error).message);
      setRows([]);
    }
  }, []);

  useEffect(() => {
    load(agentFilter.trim());
  }, [load, agentFilter]);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
          <p className="text-sm text-muted-foreground">
            Every completed agent session with its deterministic health card.
            Click a row to open the full Session Report.
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-muted-foreground font-mono">
          agent_id
          <input
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            placeholder="(all agents)"
            className="rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice w-48"
          />
        </label>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent sessions</CardTitle>
        </CardHeader>
        <CardContent className="p-0 overflow-auto">
          {error && (
            <p className="p-6 text-xs text-safer-critical font-mono">{error}</p>
          )}
          {!rows && !error && (
            <p className="p-6 text-xs text-muted-foreground font-mono">
              loading…
            </p>
          )}
          {rows && rows.length === 0 && !error && (
            <p className="p-6 text-xs text-muted-foreground font-mono">
              No sessions yet. Run an instrumented agent to see activity here.
            </p>
          )}
          {rows && rows.length > 0 && (
            <table className="w-full text-sm">
              <thead className="bg-card border-b border-border">
                <tr className="text-left text-xs text-muted-foreground font-normal">
                  <th className="p-3 w-48">session</th>
                  <th className="p-3 w-40">agent</th>
                  <th className="p-3 w-28">started</th>
                  <th className="p-3 w-20">duration</th>
                  <th className="p-3 w-16 text-right">steps</th>
                  <th className="p-3 w-20">health</th>
                  <th className="p-3 w-16 text-right">cost</th>
                  <th className="p-3 w-6" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.session_id}
                    className="border-b border-border/40 hover:bg-muted/30 font-mono"
                  >
                    <td className="p-3">
                      <Link
                        to={`/sessions/${encodeURIComponent(r.session_id)}`}
                        className="text-safer-ice hover:underline"
                      >
                        {r.session_id}
                      </Link>
                    </td>
                    <td className="p-3 text-muted-foreground truncate">
                      {r.agent_name} · {r.agent_id}
                    </td>
                    <td className="p-3 text-xs text-muted-foreground">
                      {new Date(r.started_at).toLocaleString()}
                    </td>
                    <td className="p-3 text-xs">
                      {fmtDuration(r.started_at, r.ended_at)}
                    </td>
                    <td className="p-3 text-right text-xs">{r.total_steps}</td>
                    <td className="p-3">
                      <span className={cn("font-semibold", healthTone(r.overall_health))}>
                        {r.overall_health ?? "—"}
                      </span>
                      {!r.success && (
                        <Badge className="ml-2" variant="critical">fail</Badge>
                      )}
                    </td>
                    <td className="p-3 text-right text-xs">
                      ${r.total_cost_usd.toFixed(4)}
                    </td>
                    <td className="p-3 text-muted-foreground">
                      <ChevronRight className="h-4 w-4" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
