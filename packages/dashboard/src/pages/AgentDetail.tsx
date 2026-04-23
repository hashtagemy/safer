import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  Microscope,
  Sparkles,
  Target,
  User,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { InspectorReportView } from "@/components/inspector/InspectorReportView";
import {
  AgentRecord,
  AgentRedTeamRow,
  AgentSessionRow,
  getAgent,
  getAgentRedTeamReports,
  getAgentSessions,
  getLatestScan,
  scanAgent,
} from "@/lib/agents-api";
import type { InspectorReport } from "@/lib/inspector-types";
import { cn } from "@/lib/utils";

type Tab = "identity" | "inspector" | "sessions" | "redteam";

const TABS: Array<{ id: Tab; label: string; Icon: typeof User }> = [
  { id: "identity", label: "Identity", Icon: User },
  { id: "inspector", label: "Inspector Report", Icon: Microscope },
  { id: "sessions", label: "Sessions & Reports", Icon: FileText },
  { id: "redteam", label: "Red-Team Reports", Icon: Target },
];

export default function AgentDetail() {
  const { agentId: raw } = useParams<{ agentId: string }>();
  const agentId = raw ?? "";
  const [tab, setTab] = useState<Tab>("identity");
  const [record, setRecord] = useState<AgentRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refetchRecord = useCallback(async () => {
    if (!agentId) return;
    try {
      setRecord(await getAgent(agentId));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [agentId]);

  useEffect(() => {
    refetchRecord();
  }, [refetchRecord]);

  if (!agentId) return null;

  return (
    <div className="p-6 space-y-4">
      <Link
        to="/agents"
        className="inline-flex items-center gap-2 text-xs text-muted-foreground font-mono hover:text-foreground"
      >
        <ArrowLeft className="h-3 w-3" /> Back to agents
      </Link>

      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {record?.name ?? agentId}
          </h1>
          <div className="text-xs text-muted-foreground font-mono flex items-center gap-2 mt-1">
            <code>{agentId}</code>
            {record?.framework && <Badge variant="outline">{record.framework}</Badge>}
            {record?.version && <span>v{record.version}</span>}
          </div>
        </div>
      </header>

      {error && (
        <Card>
          <CardContent className="p-4 text-xs text-safer-critical font-mono flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="break-all">{error}</span>
          </CardContent>
        </Card>
      )}

      <div className="flex gap-1 border-b border-border">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={cn(
              "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-mono transition border-b-2 -mb-px",
              tab === id
                ? "border-safer-ice text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {tab === "identity" && <IdentityTab record={record} />}
      {tab === "inspector" && (
        <InspectorTab agentId={agentId} onScanComplete={refetchRecord} />
      )}
      {tab === "sessions" && <SessionsTab agentId={agentId} />}
      {tab === "redteam" && <RedTeamTab agentId={agentId} />}
    </div>
  );
}

// ============================================================
// Identity tab
// ============================================================

function IdentityTab({ record }: { record: AgentRecord | null }) {
  if (!record) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground font-mono">
          Loading…
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <User className="h-4 w-4 text-safer-ice" /> Identity
          </CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[max-content,1fr] gap-x-4 gap-y-1 text-xs font-mono">
            <dt className="text-muted-foreground">agent_id</dt>
            <dd className="break-all">{record.agent_id}</dd>
            <dt className="text-muted-foreground">name</dt>
            <dd className="break-all">{record.name}</dd>
            <dt className="text-muted-foreground">framework</dt>
            <dd>{record.framework ?? "—"}</dd>
            <dt className="text-muted-foreground">version</dt>
            <dd>{record.version ?? "—"}</dd>
            <dt className="text-muted-foreground">first seen</dt>
            <dd>{formatDate(record.created_at)}</dd>
            <dt className="text-muted-foreground">registered</dt>
            <dd>{formatDate(record.registered_at)}</dd>
            <dt className="text-muted-foreground">last seen</dt>
            <dd>{formatDate(record.last_seen_at)}</dd>
            <dt className="text-muted-foreground">risk_score</dt>
            <dd>{record.latest_scan_id ? `${record.risk_score}/100` : "not scanned yet"}</dd>
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-safer-ice" /> Code snapshot
          </CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[max-content,1fr] gap-x-4 gap-y-1 text-xs font-mono">
            <dt className="text-muted-foreground">project_root</dt>
            <dd className="break-all">{record.project_root ?? "—"}</dd>
            <dt className="text-muted-foreground">file_count</dt>
            <dd>{record.file_count}</dd>
            <dt className="text-muted-foreground">total_bytes</dt>
            <dd>{record.total_bytes.toLocaleString()}</dd>
            <dt className="text-muted-foreground">truncated</dt>
            <dd>{record.snapshot_truncated ? "yes" : "no"}</dd>
            <dt className="text-muted-foreground">hash</dt>
            <dd className="break-all">
              {record.code_snapshot_hash
                ? record.code_snapshot_hash.slice(0, 16) + "…"
                : "—"}
            </dd>
          </dl>
          <p className="mt-3 text-[11px] text-muted-foreground font-mono">
            Snapshot refreshes each time the agent process starts. To force a
            refresh after editing code, restart the agent.
          </p>
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-base">System prompt</CardTitle>
        </CardHeader>
        <CardContent>
          {record.system_prompt ? (
            <pre className="text-[11px] font-mono bg-muted/40 rounded p-3 whitespace-pre-wrap break-words">
              {record.system_prompt}
            </pre>
          ) : (
            <p className="text-xs text-muted-foreground font-mono">
              No system prompt captured yet. Pass{" "}
              <code>instrument(system_prompt=…)</code> or let the Claude adapter
              observe it on the first messages.create call.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ============================================================
// Inspector tab
// ============================================================

function InspectorTab({
  agentId,
  onScanComplete,
}: {
  agentId: string;
  onScanComplete: () => void;
}) {
  const [report, setReport] = useState<InspectorReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const existing = (await getLatestScan(agentId)) as InspectorReport | null;
        if (!cancelled) setReport(existing);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  const runScan = async () => {
    setScanning(true);
    setError(null);
    try {
      const r = (await scanAgent(agentId, { skip_persona_review: false })) as InspectorReport;
      setReport(r);
      onScanComplete();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-4 flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm">
            {report ? (
              <>
                <span className="font-semibold">Last scan:</span>{" "}
                <span className="text-muted-foreground font-mono">
                  {formatDate(report.created_at)}
                </span>
              </>
            ) : loading ? (
              <span className="text-muted-foreground font-mono">
                Checking for an existing scan…
              </span>
            ) : (
              <span className="text-muted-foreground font-mono">
                No scan yet. Run an Inspector scan to review the codebase for
                prompt injection, data leakage, compliance, and security issues.
              </span>
            )}
          </div>
          <button
            onClick={runScan}
            disabled={scanning}
            className="inline-flex items-center gap-2 rounded-md bg-safer-ice px-3 py-2 text-sm font-medium text-safer-bg hover:opacity-90 disabled:opacity-50 transition"
          >
            <Sparkles className="h-4 w-4" />
            {scanning ? "Scanning codebase…" : report ? "Re-scan" : "Scan codebase"}
          </button>
        </CardContent>
      </Card>

      {error && (
        <Card>
          <CardContent className="p-4 text-xs text-safer-critical font-mono flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="break-all">{error}</span>
          </CardContent>
        </Card>
      )}

      {report && <InspectorReportView report={report} />}
    </div>
  );
}

// ============================================================
// Sessions & Reports tab
// ============================================================

function SessionsTab({ agentId }: { agentId: string }) {
  const [rows, setRows] = useState<AgentSessionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getAgentSessions(agentId);
        if (!cancelled) setRows(data);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  if (error) {
    return (
      <Card>
        <CardContent className="p-4 text-xs text-safer-critical font-mono">
          {error}
        </CardContent>
      </Card>
    );
  }
  if (rows === null) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground font-mono">
          Loading sessions…
        </CardContent>
      </Card>
    );
  }
  if (rows.length === 0) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground font-mono">
          No sessions yet. Once this agent runs, completed sessions will appear
          here with their Session Reports.
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardContent className="p-0">
        <ul className="divide-y divide-border">
          {rows.map((r) => {
            const open = !!expanded[r.session_id];
            return (
              <li key={r.session_id} className="p-3">
                <button
                  onClick={() =>
                    setExpanded((prev) => ({
                      ...prev,
                      [r.session_id]: !prev[r.session_id],
                    }))
                  }
                  className="w-full text-left flex items-center gap-3 text-xs font-mono"
                >
                  {open ? (
                    <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                  )}
                  <span className="break-all flex-1">{r.session_id}</span>
                  <span className="text-muted-foreground">
                    {r.total_steps} steps
                  </span>
                  <span className="text-muted-foreground">
                    {formatDate(r.ended_at ?? r.started_at)}
                  </span>
                  {r.overall_health !== null && (
                    <Badge
                      variant={
                        r.overall_health >= 70
                          ? "success"
                          : r.overall_health >= 40
                          ? "warning"
                          : "critical"
                      }
                    >
                      health {r.overall_health}
                    </Badge>
                  )}
                  {!r.success && <Badge variant="critical">failed</Badge>}
                </button>
                {open && (
                  <div className="mt-2 ml-6 text-[11px] font-mono grid grid-cols-[max-content,1fr] gap-x-4 gap-y-0.5 text-muted-foreground">
                    <dt>started</dt>
                    <dd>{formatDate(r.started_at)}</dd>
                    <dt>ended</dt>
                    <dd>{formatDate(r.ended_at)}</dd>
                    <dt>cost</dt>
                    <dd>${r.total_cost_usd.toFixed(4)}</dd>
                    <dt>report</dt>
                    <dd>
                      {r.has_report ? (
                        <Link
                          to={`/sessions/${encodeURIComponent(r.session_id)}`}
                          className="inline-flex items-center gap-1 text-safer-ice hover:underline"
                        >
                          Open full report <ExternalLink className="h-3 w-3" />
                        </Link>
                      ) : (
                        <span>not generated yet</span>
                      )}
                    </dd>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

// ============================================================
// Red-Team tab
// ============================================================

function RedTeamTab({ agentId }: { agentId: string }) {
  const [rows, setRows] = useState<AgentRedTeamRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getAgentRedTeamReports(agentId);
        if (!cancelled) setRows(data);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-3 flex items-center justify-between gap-3 flex-wrap">
          <p className="text-xs text-muted-foreground font-mono">
            Red-Team runs are triggered manually from the{" "}
            <Link to="/redteam" className="text-safer-ice hover:underline">
              Red-Team page
            </Link>
            . Their reports land here for this agent.
          </p>
          <Link
            to={`/redteam?agent=${encodeURIComponent(agentId)}`}
            className="inline-flex items-center gap-1 text-xs font-mono text-safer-ice hover:underline"
          >
            Open Red-Team <ExternalLink className="h-3 w-3" />
          </Link>
        </CardContent>
      </Card>

      {error && (
        <Card>
          <CardContent className="p-4 text-xs text-safer-critical font-mono">
            {error}
          </CardContent>
        </Card>
      )}

      {rows === null && !error && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground font-mono">
            Loading red-team reports…
          </CardContent>
        </Card>
      )}

      {rows !== null && rows.length === 0 && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground font-mono">
            No red-team runs yet. Head to{" "}
            <Link to="/redteam" className="text-safer-ice hover:underline">
              /redteam
            </Link>{" "}
            to launch one.
          </CardContent>
        </Card>
      )}

      {rows !== null && rows.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <ul className="divide-y divide-border">
              {rows.map((r) => (
                <li key={r.run_id} className="p-3 text-xs font-mono">
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="break-all flex-1">{r.run_id}</span>
                    <Badge variant="outline">{r.mode}</Badge>
                    <Badge
                      variant={
                        r.phase === "done"
                          ? "success"
                          : r.phase === "failed"
                          ? "critical"
                          : "ice"
                      }
                    >
                      {r.phase}
                    </Badge>
                    <span className="text-muted-foreground">
                      {r.findings_count} findings
                    </span>
                    <span className="text-muted-foreground">
                      safety {r.safety_score}
                    </span>
                    <span className="text-muted-foreground">
                      {formatDate(r.finished_at ?? r.started_at)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ============================================================
// helpers
// ============================================================

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString();
}
