import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { fetchJSON } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { SessionListItem, SessionReport } from "@/lib/sessionTypes";

type Variant = "ice" | "success" | "warning" | "critical" | "muted" | "outline";

interface ListResponse {
  sessions: SessionListItem[];
}

function healthTone(v: number | null): string {
  if (v === null) return "bg-muted";
  if (v >= 90) return "bg-safer-success";
  if (v >= 70) return "bg-safer-ice";
  if (v >= 40) return "bg-safer-warning";
  return "bg-safer-critical";
}

function categoryTone(v: number): Variant {
  if (v >= 90) return "success";
  if (v >= 70) return "ice";
  if (v >= 40) return "warning";
  return "critical";
}

interface QualityRow {
  session: SessionListItem;
  report: SessionReport | null;
  error: string | null;
}

export default function Quality() {
  const [sessions, setSessions] = useState<SessionListItem[] | null>(null);
  const [rows, setRows] = useState<QualityRow[]>([]);
  const [agentFilter, setAgentFilter] = useState("");
  const [listError, setListError] = useState<string | null>(null);

  const loadSessions = useCallback(async (agent: string) => {
    setListError(null);
    try {
      const qs = agent ? `?agent_id=${encodeURIComponent(agent)}&limit=15` : "?limit=15";
      const r = await fetchJSON<ListResponse>(`/v1/sessions${qs}`);
      setSessions(r.sessions);
    } catch (e) {
      setListError((e as Error).message);
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    loadSessions(agentFilter.trim());
  }, [loadSessions, agentFilter]);

  useEffect(() => {
    if (!sessions) {
      setRows([]);
      return;
    }
    let cancelled = false;
    const load = async () => {
      const results: QualityRow[] = await Promise.all(
        sessions.map(async (s) => {
          try {
            const report = await fetchJSON<SessionReport>(
              `/v1/sessions/${encodeURIComponent(s.session_id)}/report`
            );
            return { session: s, report, error: null };
          } catch (e) {
            return { session: s, report: null, error: (e as Error).message };
          }
        })
      );
      if (!cancelled) setRows(results);
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [sessions]);

  const withReports = rows.filter((r) => r.report !== null);
  const avgHealth =
    withReports.length > 0
      ? Math.round(
          withReports.reduce((acc, r) => acc + (r.report!.overall_health || 0), 0) /
            withReports.length
        )
      : null;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Quality</h1>
          <p className="text-sm text-muted-foreground">
            Rolling health per session: overall score, category fingerprints,
            and Quality-Reviewer signals (hallucinations, efficiency, goal drift).
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
        <CardContent className="p-4 flex flex-wrap items-center gap-6">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
              avg health (last {withReports.length})
            </div>
            <div
              className={cn(
                "text-3xl font-semibold tabular-nums",
                avgHealth === null
                  ? "text-muted-foreground"
                  : avgHealth >= 90
                  ? "text-safer-success"
                  : avgHealth >= 70
                  ? "text-safer-ice"
                  : avgHealth >= 40
                  ? "text-safer-warning"
                  : "text-safer-critical"
              )}
            >
              {avgHealth ?? "—"}
            </div>
          </div>
          <HealthSparkline rows={rows} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent sessions (quality view)</CardTitle>
        </CardHeader>
        <CardContent className="p-0 overflow-auto">
          {listError && (
            <p className="p-6 text-xs text-safer-critical font-mono">{listError}</p>
          )}
          {sessions === null && !listError && (
            <p className="p-6 text-xs text-muted-foreground font-mono">loading…</p>
          )}
          {sessions && sessions.length === 0 && !listError && (
            <p className="p-6 text-xs text-muted-foreground font-mono">
              No sessions yet.
            </p>
          )}
          {sessions && sessions.length > 0 && (
            <table className="w-full text-sm">
              <thead className="bg-card border-b border-border">
                <tr className="text-left text-xs text-muted-foreground font-normal">
                  <th className="p-3 w-48">session</th>
                  <th className="p-3 w-16 text-right">health</th>
                  <th className="p-3">security · compliance · trust · scope · ethics · policy · quality</th>
                  <th className="p-3 w-48">top concern</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <QualityRowView key={r.session.session_id} row={r} />
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function HealthSparkline({ rows }: { rows: QualityRow[] }) {
  if (rows.length === 0) return null;
  const values = rows.map((r) => r.report?.overall_health ?? 0).reverse();
  const w = Math.max(200, values.length * 18);
  const max = 100;
  return (
    <div className="flex items-end gap-1 h-14">
      {values.map((v, i) => {
        const h = Math.max(2, (v / max) * 52);
        return (
          <div
            key={i}
            className={cn("w-3 rounded-sm", healthTone(v))}
            style={{ height: `${h}px`, width: `${Math.floor(w / values.length)}px` }}
            title={`${v}`}
          />
        );
      })}
    </div>
  );
}

function QualityRowView({ row }: { row: QualityRow }) {
  const { session, report } = row;
  const categoryValue = (name: string) =>
    report?.categories.find((c) => c.name === name)?.value ?? null;
  const topFinding = report?.top_findings?.[0];

  return (
    <tr className="border-b border-border/40 hover:bg-muted/30 font-mono">
      <td className="p-3">
        <Link
          to={`/sessions/${encodeURIComponent(session.session_id)}`}
          className="text-safer-ice hover:underline"
        >
          {session.session_id}
        </Link>
        <div className="text-[11px] text-muted-foreground truncate">
          {session.agent_name}
        </div>
      </td>
      <td className="p-3 text-right">
        {report ? (
          <span
            className={cn(
              "font-semibold",
              report.overall_health >= 90
                ? "text-safer-success"
                : report.overall_health >= 70
                ? "text-safer-ice"
                : report.overall_health >= 40
                ? "text-safer-warning"
                : "text-safer-critical"
            )}
          >
            {report.overall_health}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="p-3">
        <div className="flex items-center gap-1 flex-wrap">
          {["security", "compliance", "trust", "scope", "ethics", "policy_warden", "quality"].map(
            (c) => {
              const v = categoryValue(c);
              if (v === null) {
                return (
                  <Badge key={c} variant="muted">
                    —
                  </Badge>
                );
              }
              return (
                <Badge key={c} variant={categoryTone(v)}>
                  {v}
                </Badge>
              );
            }
          )}
        </div>
      </td>
      <td className="p-3 text-xs text-muted-foreground truncate">
        {topFinding ? (
          <>
            <Badge variant={categoryTone(topFinding.severity === "CRITICAL" ? 20 : topFinding.severity === "HIGH" ? 50 : 70)}>
              {topFinding.severity}
            </Badge>{" "}
            {topFinding.flag}
          </>
        ) : report ? (
          <span className="text-safer-success">clean</span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
    </tr>
  );
}
