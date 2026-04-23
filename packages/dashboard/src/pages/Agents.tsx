import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  Boxes,
  CheckCircle2,
  CircleSlash,
  Radio,
  Sparkles,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { WS_URL } from "@/lib/api";
import {
  AgentSummary,
  listAgents,
  ScanStatus,
} from "@/lib/agents-api";
import { cn } from "@/lib/utils";

type Variant =
  | "ice"
  | "success"
  | "warning"
  | "critical"
  | "muted"
  | "outline";

const scanVariant: Record<ScanStatus, Variant> = {
  unscanned: "muted",
  scanning: "ice",
  scanned: "success",
};

export default function Agents() {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const refetchTimer = useRef<number | undefined>(undefined);

  const refetch = useMemo(
    () => async () => {
      try {
        const data = await listAgents();
        setAgents(data);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    refetch();
  }, [refetch]);

  // Live card updates — a tiny WS listener just for registry events.
  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;

    const scheduleRefetch = () => {
      if (refetchTimer.current) window.clearTimeout(refetchTimer.current);
      refetchTimer.current = window.setTimeout(() => {
        if (!cancelled) refetch();
      }, 150);
    };

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(`${WS_URL}/ws/stream`);
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (
            data?.type === "agent_registered" ||
            data?.type === "agent_profile_patched" ||
            data?.type === "inspector_report_ready"
          ) {
            scheduleRefetch();
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        if (!cancelled) window.setTimeout(connect, 2000);
      };
      ws.onerror = () => ws?.close();
    };
    connect();

    return () => {
      cancelled = true;
      if (refetchTimer.current) window.clearTimeout(refetchTimer.current);
      ws?.close();
    };
  }, [refetch]);

  return (
    <div className="p-6 space-y-6">
      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
          <p className="text-sm text-muted-foreground">
            Every agent that calls{" "}
            <code className="rounded bg-muted/40 px-1 py-0.5 text-xs">
              safer.instrument()
            </code>{" "}
            shows up here automatically. Open a card to see the agent's
            identity, run an Inspector scan over its codebase, browse session
            reports, and review past red-team runs.
          </p>
        </div>
      </header>

      {loading && agents === null && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground font-mono">
            Loading agents…
          </CardContent>
        </Card>
      )}

      {error && (
        <Card>
          <CardContent className="p-4 text-xs text-safer-critical font-mono flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="break-all">{error}</span>
          </CardContent>
        </Card>
      )}

      {agents !== null && agents.length === 0 && !error && <EmptyState />}

      {agents !== null && agents.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {agents.map((a) => (
            <AgentCard key={a.agent_id} agent={a} />
          ))}
        </div>
      )}
    </div>
  );
}

function AgentCard({ agent }: { agent: AgentSummary }) {
  return (
    <Link to={`/agents/${encodeURIComponent(agent.agent_id)}`} className="group">
      <Card className="h-full transition group-hover:border-safer-ice/60 group-hover:bg-card/70">
        <CardContent className="p-4 space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-semibold text-base truncate">{agent.name}</div>
              <div className="text-[11px] text-muted-foreground font-mono truncate">
                {agent.agent_id}
              </div>
            </div>
            <Badge variant={scanVariant[agent.scan_status]}>
              {agent.scan_status}
            </Badge>
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
            <dt className="text-muted-foreground flex items-center gap-1">
              <Boxes className="h-3 w-3" /> framework
            </dt>
            <dd>{agent.framework ?? "—"}</dd>
            <dt className="text-muted-foreground flex items-center gap-1">
              <Sparkles className="h-3 w-3" /> files
            </dt>
            <dd>{agent.file_count}</dd>
            <dt className="text-muted-foreground flex items-center gap-1">
              <Activity className="h-3 w-3" /> last seen
            </dt>
            <dd className="truncate">{formatRelative(agent.last_seen_at)}</dd>
            <dt className="text-muted-foreground flex items-center gap-1">
              {agent.scan_status === "scanned" ? (
                <CheckCircle2 className="h-3 w-3 text-safer-success" />
              ) : (
                <CircleSlash className="h-3 w-3" />
              )}{" "}
              risk
            </dt>
            <dd
              className={cn(
                agent.risk_score < 40 && "text-safer-critical",
                agent.risk_score >= 40 && agent.risk_score < 70 && "text-safer-warning"
              )}
            >
              {agent.latest_scan_id ? `${agent.risk_score}/100` : "—"}
            </dd>
          </dl>
        </CardContent>
      </Card>
    </Link>
  );
}

function EmptyState() {
  return (
    <Card>
      <CardContent className="p-8 text-center space-y-3">
        <Radio className="h-8 w-8 mx-auto text-muted-foreground" />
        <div className="text-sm font-semibold">No agents connected yet</div>
        <p className="text-xs text-muted-foreground font-mono max-w-md mx-auto">
          Add one line to your Python agent and run it:
        </p>
        <pre className="mx-auto inline-block text-left text-[11px] bg-muted/40 rounded p-3 font-mono">
          {`from safer import instrument\n\ninstrument(agent_id="my-agent")`}
        </pre>
        <p className="text-xs text-muted-foreground font-mono">
          SAFER will auto-register the agent and its codebase will appear here.
        </p>
      </CardContent>
    </Card>
  );
}

function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 30) return "just now";
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  const mins = secs / 60;
  if (mins < 60) return `${Math.floor(mins)}m ago`;
  const hours = mins / 60;
  if (hours < 24) return `${Math.floor(hours)}h ago`;
  const days = hours / 24;
  return `${Math.floor(days)}d ago`;
}
