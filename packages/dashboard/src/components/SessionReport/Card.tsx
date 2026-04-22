import {
  RotateCw,
  Sparkles,
  FileDown,
  ShieldAlert,
  AlertTriangle,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import type { SessionReport, Severity } from "@/lib/sessionTypes";
import { Gauge } from "./Gauge";

type Variant = "ice" | "success" | "warning" | "critical" | "muted" | "outline";

const severityVariant: Record<Severity, Variant> = {
  LOW: "success",
  MEDIUM: "ice",
  HIGH: "warning",
  CRITICAL: "critical",
};

const CATEGORY_LABELS: Record<string, string> = {
  security: "Security",
  compliance: "Compliance",
  trust: "Trust",
  scope: "Scope",
  ethics: "Ethics",
  policy_warden: "Policy Warden",
  quality: "Quality",
};

const OWASP_ROWS: Array<{ id: string; label: string }> = [
  { id: "owasp_llm01_prompt_injection", label: "LLM01" },
  { id: "owasp_llm02_insecure_output_handling", label: "LLM02" },
  { id: "owasp_llm03_training_data_poisoning", label: "LLM03" },
  { id: "owasp_llm04_model_denial_of_service", label: "LLM04" },
  { id: "owasp_llm05_supply_chain", label: "LLM05" },
  { id: "owasp_llm06_sensitive_info_disclosure", label: "LLM06" },
  { id: "owasp_llm07_insecure_plugin_design", label: "LLM07" },
  { id: "owasp_llm08_excessive_agency", label: "LLM08" },
  { id: "owasp_llm09_overreliance", label: "LLM09" },
  { id: "owasp_llm10_model_theft", label: "LLM10" },
];

function scoreTone(v: number): string {
  if (v >= 90) return "bg-safer-success";
  if (v >= 70) return "bg-safer-ice";
  if (v >= 40) return "bg-safer-warning";
  return "bg-safer-critical";
}

function fmtDuration(ms: number): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`;
}

export interface SessionReportCardProps {
  report: SessionReport;
  regenerating: boolean;
  reconstructing: boolean;
  onRegenerate: () => void;
  onReconstruct: () => void;
}

export function SessionReportCard({
  report,
  regenerating,
  reconstructing,
  onRegenerate,
  onReconstruct,
}: SessionReportCardProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <CardTitle className="text-base">Session report</CardTitle>
            <p className="text-xs text-muted-foreground font-mono">
              {report.agent_name} · {report.session_id} · generated{" "}
              {new Date(report.generated_at).toLocaleString()}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onReconstruct}
              disabled={reconstructing}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-mono hover:bg-muted/40 disabled:opacity-50 transition"
              title="Force a Thought-Chain reconstruction (Opus call)"
            >
              <Sparkles className="h-3.5 w-3.5" />
              {reconstructing ? "Reconstructing…" : "Reconstruct"}
            </button>
            <button
              onClick={onRegenerate}
              disabled={regenerating}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-mono hover:bg-muted/40 disabled:opacity-50 transition"
              title="Regenerate the report (Quality + deterministic aggregator)"
            >
              <RotateCw className={cn("h-3.5 w-3.5", regenerating && "animate-spin")} />
              Regenerate
            </button>
            <ExportJsonButton report={report} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-start gap-6">
          <Gauge value={report.overall_health} />

          <div className="flex-1 min-w-[260px] grid grid-cols-2 gap-x-6 gap-y-1 text-xs font-mono">
            <dt className="text-muted-foreground">agent_id</dt>
            <dd className="break-all">{report.agent_id}</dd>
            <dt className="text-muted-foreground">total steps</dt>
            <dd>{report.total_steps}</dd>
            <dt className="text-muted-foreground">duration</dt>
            <dd>{fmtDuration(report.duration_ms)}</dd>
            <dt className="text-muted-foreground">success</dt>
            <dd>{report.success ? "yes" : "no"}</dd>
            <dt className="text-muted-foreground">cost</dt>
            <dd>${report.cost.total_usd.toFixed(4)}</dd>
            <dt className="text-muted-foreground">opus / haiku calls</dt>
            <dd>
              {report.cost.num_opus_calls} / {report.cost.num_haiku_calls}
            </dd>
          </div>
        </div>

        <CategoryBars categories={report.categories} />
        <TopFindingsList findings={report.top_findings} />
        <OwaspMiniGrid map={report.owasp_map} />
        {report.red_team_summary && <RedTeamMini summary={report.red_team_summary} />}
      </CardContent>
    </Card>
  );
}

function CategoryBars({
  categories,
}: {
  categories: SessionReport["categories"];
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
        Category scores
      </div>
      <div className="space-y-1.5">
        {categories.map((c) => (
          <div key={c.name} className="flex items-center gap-3">
            <div className="w-28 text-xs font-mono text-muted-foreground">
              {CATEGORY_LABELS[c.name] ?? c.name}
            </div>
            <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
              <div
                className={cn("h-full rounded-full transition-all", scoreTone(c.value))}
                style={{ width: `${c.value}%` }}
              />
            </div>
            <div className="w-10 text-right text-xs font-mono tabular-nums">
              {c.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TopFindingsList({
  findings,
}: {
  findings: SessionReport["top_findings"];
}) {
  if (!findings.length) return null;
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
        Top findings
      </div>
      <div className="space-y-1.5">
        {findings.map((f, i) => (
          <div
            key={i}
            className="flex items-start gap-2 rounded-md border border-border bg-card/40 p-2 text-xs"
          >
            <Badge variant={severityVariant[f.severity]}>{f.severity}</Badge>
            <Badge variant="outline">{f.flag}</Badge>
            <span className="text-muted-foreground font-mono truncate">
              {f.summary}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function OwaspMiniGrid({ map }: { map: Record<string, number> }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
        OWASP LLM Top 10
      </div>
      <div className="grid grid-cols-5 gap-1.5">
        {OWASP_ROWS.map((row) => {
          const c = map[row.id] ?? 0;
          const tone =
            c === 0
              ? "bg-muted/30 text-muted-foreground border-border"
              : c >= 3
              ? "bg-safer-critical/10 text-safer-critical border-safer-critical/40"
              : "bg-safer-warning/10 text-safer-warning border-safer-warning/40";
          return (
            <div
              key={row.id}
              className={cn(
                "rounded-md border px-2 py-1 flex items-center justify-between text-[11px] font-mono",
                tone
              )}
              title={row.id}
            >
              <span>{row.label}</span>
              <span className="font-semibold">{c}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RedTeamMini({
  summary,
}: {
  summary: NonNullable<SessionReport["red_team_summary"]>;
}) {
  return (
    <div className="rounded-md border border-border bg-card/40 p-3 flex items-center gap-3 text-xs font-mono">
      <ShieldAlert className="h-4 w-4 text-safer-critical" />
      <span className="text-muted-foreground">red-team</span>
      <span>safety {summary.safety_score}</span>
      <span className="text-muted-foreground">·</span>
      <span>{summary.findings_count} findings</span>
      <span className="ml-auto text-muted-foreground">
        {new Date(summary.ran_at).toLocaleDateString()}
      </span>
    </div>
  );
}

function ExportJsonButton({ report }: { report: SessionReport }) {
  return (
    <button
      onClick={() => {
        const blob = new Blob([JSON.stringify(report, null, 2)], {
          type: "application/json",
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `session-${report.session_id}.json`;
        a.click();
        URL.revokeObjectURL(url);
      }}
      className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-mono hover:bg-muted/40 transition"
    >
      <FileDown className="h-3.5 w-3.5" />
      Export JSON
    </button>
  );
}

export function ReportLoadError({ message }: { message: string }) {
  return (
    <Card>
      <CardContent className="p-6 flex items-start gap-2 text-xs text-safer-critical font-mono">
        <AlertTriangle className="h-4 w-4 shrink-0" />
        <span className="break-all">{message}</span>
      </CardContent>
    </Card>
  );
}
