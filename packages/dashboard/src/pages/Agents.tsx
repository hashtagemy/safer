import { useState } from "react";
import {
  Microscope,
  AlertTriangle,
  CheckCircle2,
  FileCode,
  Wrench,
  MessageSquare,
  Sparkles,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Gauge } from "@/components/SessionReport/Gauge";
import { BACKEND_URL } from "@/lib/api";
import { cn } from "@/lib/utils";

type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
type Variant = "ice" | "success" | "warning" | "critical" | "muted" | "outline";
type RiskClass = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

interface ToolSpec {
  name: string;
  signature: string;
  docstring: string | null;
  decorators: string[];
  risk_class: RiskClass;
  risk_reason: string;
}

interface LLMCallSite {
  provider: string;
  function: string;
  line: number;
}

interface ASTSummary {
  module: string;
  tools: ToolSpec[];
  llm_calls: LLMCallSite[];
  entry_points: string[];
  imports: string[];
  loc: number;
  parse_error: string | null;
}

interface PatternMatch {
  rule_id: string;
  severity: Severity;
  flag: string;
  line: number;
  snippet: string;
  message: string;
}

interface Finding {
  finding_id: string;
  severity: Severity;
  category: string;
  flag: string;
  title: string;
  description: string;
  evidence: string[];
  reproduction_steps: string[];
  recommended_mitigation: string | null;
}

interface PolicySuggestion {
  suggestion_id: string;
  name: string;
  reason: string;
  natural_language: string;
  triggering_flags: string[];
  severity: Severity;
}

interface InspectorReport {
  report_id: string;
  agent_id: string;
  created_at: string;
  risk_score: number;
  risk_level: Severity;
  ast_summary: ASTSummary;
  pattern_matches: PatternMatch[];
  findings: Finding[];
  policy_suggestions: PolicySuggestion[];
  duration_ms: number;
  persona_review_skipped: boolean;
  persona_review_error: string | null;
}

const severityVariant: Record<Severity, Variant> = {
  LOW: "success",
  MEDIUM: "ice",
  HIGH: "warning",
  CRITICAL: "critical",
};

const riskClassVariant: Record<RiskClass, Variant> = {
  LOW: "success",
  MEDIUM: "ice",
  HIGH: "warning",
  CRITICAL: "critical",
};

const SAMPLE_SOURCE = `import os
import subprocess
import requests

API_KEY = "sk-ant-demo-DO-NOT-USE-DO-NOT-USE-DONOT"

@tool
def send_email(to: str, body: str) -> dict:
    """Email a customer."""
    return {"sent": True}

@tool
def get_order(id: str) -> dict:
    """Look up an order."""
    return {"id": id}

@tool
def run_shell(cmd: str) -> str:
    """Run a shell command on the server."""
    return subprocess.run("bash -c " + cmd, shell=True, capture_output=True).stdout.decode()

def main():
    resp = requests.get("http://internal.example/orders", verify=False)
    print(resp.text)
`;

export default function Agents() {
  const [source, setSource] = useState(SAMPLE_SOURCE);
  const [systemPrompt, setSystemPrompt] = useState(
    "You are a customer-support agent. Never email data to external domains."
  );
  const [agentId, setAgentId] = useState("agent_demo");
  const [skipPersona, setSkipPersona] = useState(false);

  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<InspectorReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runInspector = async () => {
    if (!agentId.trim() || !source.trim()) return;
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      const r = await fetch(
        `${BACKEND_URL}/v1/agents/${encodeURIComponent(agentId.trim())}/inspect`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source,
            system_prompt: systemPrompt,
            skip_persona_review: skipPersona,
          }),
        }
      );
      if (!r.ok) {
        throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
      }
      setReport((await r.json()) as InspectorReport);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
          <p className="text-sm text-muted-foreground">
            Onboarding-phase Inspector. Paste an agent's source, hit Run —
            SAFER scans the AST, runs 12 deterministic patterns, and (if
            configured) asks three Judge personas for a review in a single
            Opus 4.7 call.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <InputColumn
          agentId={agentId}
          setAgentId={setAgentId}
          systemPrompt={systemPrompt}
          setSystemPrompt={setSystemPrompt}
          source={source}
          setSource={setSource}
          skipPersona={skipPersona}
          setSkipPersona={setSkipPersona}
          loading={loading}
          onRun={runInspector}
          error={error}
        />
        <ResultsColumn report={report} loading={loading} />
      </div>
    </div>
  );
}

function InputColumn({
  agentId,
  setAgentId,
  systemPrompt,
  setSystemPrompt,
  source,
  setSource,
  skipPersona,
  setSkipPersona,
  loading,
  onRun,
  error,
}: {
  agentId: string;
  setAgentId: (v: string) => void;
  systemPrompt: string;
  setSystemPrompt: (v: string) => void;
  source: string;
  setSource: (v: string) => void;
  skipPersona: boolean;
  setSkipPersona: (v: boolean) => void;
  loading: boolean;
  onRun: () => void;
  error: string | null;
}) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Microscope className="h-4 w-4" />
          Inspect agent
        </CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          AST + 12 patterns always run; the persona review needs
          `ANTHROPIC_API_KEY` on the backend.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="block">
          <span className="text-xs text-muted-foreground font-mono">agent_id</span>
          <input
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted-foreground font-mono">
            system_prompt (optional)
          </span>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={3}
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted-foreground font-mono">source</span>
          <textarea
            value={source}
            onChange={(e) => setSource(e.target.value)}
            rows={16}
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-muted-foreground font-mono">
          <input
            type="checkbox"
            checked={skipPersona}
            onChange={(e) => setSkipPersona(e.target.checked)}
            className="accent-safer-ice"
          />
          skip persona review (deterministic-only)
        </label>
        <button
          onClick={onRun}
          disabled={loading || !agentId.trim() || !source.trim()}
          className="w-full inline-flex items-center justify-center gap-2 rounded-md bg-safer-ice px-3 py-2 text-sm font-medium text-safer-bg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition"
        >
          <Sparkles className="h-4 w-4" />
          {loading ? "Scanning…" : "Run Inspector"}
        </button>
        {error && (
          <div className="flex items-start gap-2 text-xs text-safer-critical font-mono">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="break-all">{error}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ResultsColumn({
  report,
  loading,
}: {
  report: InspectorReport | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <Card className="h-full">
        <CardContent className="p-6 text-sm text-muted-foreground font-mono">
          Running Inspector…
        </CardContent>
      </Card>
    );
  }
  if (!report) {
    return (
      <Card className="h-full">
        <CardContent className="p-6 text-sm text-muted-foreground font-mono">
          No scan yet. Paste agent source on the left and press Run.
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <HeaderCard report={report} />
      <ASTCard summary={report.ast_summary} />
      <PatternsCard matches={report.pattern_matches} />
      <FindingsCard findings={report.findings} />
      <SuggestionsCard suggestions={report.policy_suggestions} />
    </div>
  );
}

function HeaderCard({ report }: { report: InspectorReport }) {
  return (
    <Card>
      <CardContent className="p-4 flex flex-wrap items-center gap-6">
        <Gauge value={report.risk_score} size={110} label="Risk score" />
        <div className="flex-1 min-w-[200px] grid grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
          <dt className="text-muted-foreground">risk_level</dt>
          <dd>
            <Badge variant={severityVariant[report.risk_level]}>
              {report.risk_level}
            </Badge>
          </dd>
          <dt className="text-muted-foreground">findings</dt>
          <dd>{report.findings.length}</dd>
          <dt className="text-muted-foreground">pattern matches</dt>
          <dd>{report.pattern_matches.length}</dd>
          <dt className="text-muted-foreground">policy suggestions</dt>
          <dd>{report.policy_suggestions.length}</dd>
          <dt className="text-muted-foreground">duration</dt>
          <dd>{report.duration_ms} ms</dd>
          <dt className="text-muted-foreground">persona review</dt>
          <dd>
            {report.persona_review_skipped ? (
              <span className="text-muted-foreground">skipped</span>
            ) : (
              <span className="text-safer-success inline-flex items-center gap-1">
                <CheckCircle2 className="h-3.5 w-3.5" /> ran
              </span>
            )}
          </dd>
        </div>
      </CardContent>
      {report.persona_review_error && (
        <CardContent className="pt-0">
          <p className="text-xs text-safer-warning font-mono">
            persona review: {report.persona_review_error}
          </p>
        </CardContent>
      )}
    </Card>
  );
}

function ASTCard({ summary }: { summary: ASTSummary }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <FileCode className="h-4 w-4" />
          AST summary
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {summary.parse_error ? (
          <p className="text-xs text-safer-critical font-mono">
            parse error: {summary.parse_error}
          </p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
              <dt className="text-muted-foreground">lines</dt>
              <dd>{summary.loc}</dd>
              <dt className="text-muted-foreground">entry points</dt>
              <dd>{summary.entry_points.join(", ") || "—"}</dd>
              <dt className="text-muted-foreground">imports</dt>
              <dd className="break-all">{summary.imports.join(", ") || "—"}</dd>
            </div>

            {summary.tools.length > 0 && (
              <div>
                <div className="text-[11px] uppercase text-muted-foreground mb-1 flex items-center gap-1">
                  <Wrench className="h-3 w-3" /> tools ({summary.tools.length})
                </div>
                <div className="space-y-1.5">
                  {summary.tools.map((t) => (
                    <div
                      key={t.name}
                      className="rounded-md border border-border bg-card/40 p-2 text-xs"
                    >
                      <div className="flex items-center gap-2">
                        <Badge variant={riskClassVariant[t.risk_class]}>
                          {t.risk_class}
                        </Badge>
                        <span className="font-mono break-all">{t.signature}</span>
                      </div>
                      <p className="mt-1 text-[11px] text-muted-foreground font-mono">
                        {t.risk_reason}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {summary.llm_calls.length > 0 && (
              <div>
                <div className="text-[11px] uppercase text-muted-foreground mb-1 flex items-center gap-1">
                  <MessageSquare className="h-3 w-3" /> LLM call sites (
                  {summary.llm_calls.length})
                </div>
                <ul className="text-xs font-mono space-y-0.5">
                  {summary.llm_calls.map((c, i) => (
                    <li key={i} className="flex items-center gap-2">
                      <Badge variant="outline">{c.provider}</Badge>
                      <span className="break-all">{c.function}</span>
                      <span className="text-muted-foreground ml-auto">
                        line {c.line}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function PatternsCard({ matches }: { matches: PatternMatch[] }) {
  if (matches.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pattern matches</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground font-mono">
            No deterministic pattern hits — clean on the 12 built-in rules.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Pattern matches ({matches.length})</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5">
        {matches.map((m, i) => (
          <div
            key={i}
            className="rounded-md border border-border bg-card/40 p-2 text-xs font-mono"
          >
            <div className="flex items-center gap-2">
              <Badge variant={severityVariant[m.severity]}>{m.severity}</Badge>
              <Badge variant="outline">{m.rule_id}</Badge>
              <Badge variant="outline">{m.flag}</Badge>
              <span className="text-muted-foreground ml-auto">line {m.line}</span>
            </div>
            <p className="mt-1 text-muted-foreground">{m.message}</p>
            {m.snippet && (
              <pre className="mt-1 text-[11px] bg-muted/40 rounded p-1 overflow-auto">
                {m.snippet}
              </pre>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function FindingsCard({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Findings ({findings.length})</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5">
        {findings.map((f) => (
          <div
            key={f.finding_id}
            className={cn(
              "rounded-md border p-2 text-xs",
              f.severity === "CRITICAL"
                ? "border-safer-critical/40 bg-safer-critical/5"
                : "border-border bg-card/40"
            )}
          >
            <div className="flex items-center gap-2 flex-wrap font-mono">
              <Badge variant={severityVariant[f.severity]}>{f.severity}</Badge>
              <Badge variant="outline">{f.flag}</Badge>
              <Badge variant="outline">{f.category}</Badge>
              <span className="break-all">{f.title}</span>
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">{f.description}</p>
            {f.recommended_mitigation && (
              <p className="mt-1 text-[11px] font-mono">
                <span className="text-muted-foreground">mitigation: </span>
                {f.recommended_mitigation}
              </p>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function SuggestionsCard({ suggestions }: { suggestions: PolicySuggestion[] }) {
  if (suggestions.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          Policy suggestions ({suggestions.length})
        </CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          Paste any of these into <b>/policies</b> to compile and activate.
        </p>
      </CardHeader>
      <CardContent className="space-y-2">
        {suggestions.map((s) => (
          <div
            key={s.suggestion_id}
            className="rounded-md border border-border bg-card/40 p-3"
          >
            <div className="flex items-center gap-2 flex-wrap text-xs font-mono">
              <Badge variant={severityVariant[s.severity]}>{s.severity}</Badge>
              <span className="font-semibold">{s.name}</span>
              {s.triggering_flags.map((f) => (
                <Badge key={f} variant="outline">
                  {f}
                </Badge>
              ))}
            </div>
            <p className="mt-2 text-xs">{s.natural_language}</p>
            <button
              onClick={() => {
                navigator.clipboard?.writeText(s.natural_language);
              }}
              className="mt-2 inline-flex items-center gap-1 text-[11px] text-safer-ice hover:underline font-mono"
            >
              Copy to clipboard
            </button>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
