import { useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileCode,
  Files,
  MessageSquare,
  Wrench,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { Gauge } from "@/components/SessionReport/Gauge";
import { cn } from "@/lib/utils";
import {
  ASTSummary,
  Finding,
  InspectorPersona,
  InspectorReport,
  PatternMatch,
  PolicySuggestion,
  severityVariant,
} from "@/lib/inspector-types";

const PERSONAS: Array<{ key: InspectorPersona; label: string }> = [
  { key: "security_auditor", label: "Security Auditor" },
  { key: "compliance_officer", label: "Compliance Officer" },
  { key: "policy_warden", label: "Policy Warden" },
];

type InspectorTab = "findings" | "patterns" | "suggestions" | "files";

export function InspectorReportView({ report }: { report: InspectorReport }) {
  const [tab, setTab] = useState<InspectorTab>("findings");

  const personaFindings = useMemo(
    () => report.findings.filter((f) => f.persona != null),
    [report.findings]
  );
  const findingCount = personaFindings.length;
  const patternCount = report.pattern_matches.length;
  const suggestionCount = report.policy_suggestions.length;
  const fileCount = report.scanned_files.length;

  return (
    <div className="space-y-4">
      <HeaderCard report={report} />
      <ASTCard summary={report.ast_summary} />

      <Tabs value={tab} onChange={(v) => setTab(v as InspectorTab)}>
        <TabsList>
          <TabsTrigger value="findings">Findings ({findingCount})</TabsTrigger>
          <TabsTrigger value="patterns">Patterns ({patternCount})</TabsTrigger>
          <TabsTrigger value="suggestions">
            Suggestions ({suggestionCount})
          </TabsTrigger>
          <TabsTrigger value="files">Files ({fileCount})</TabsTrigger>
        </TabsList>

        <TabsContent value="findings">
          <FindingsByPersona findings={personaFindings} />
        </TabsContent>
        <TabsContent value="patterns">
          <PatternsCard matches={report.pattern_matches} />
        </TabsContent>
        <TabsContent value="suggestions">
          <SuggestionsCard suggestions={report.policy_suggestions} />
        </TabsContent>
        <TabsContent value="files">
          <ScannedFilesCard files={report.scanned_files} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ScannedFilesCard({ files }: { files: string[] }) {
  const [open, setOpen] = useState(false);
  if (files.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Files className="h-4 w-4 text-safer-ice" />
            Scanned files
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground font-mono">
            No files were recorded for this scan.
          </p>
        </CardContent>
      </Card>
    );
  }
  const PREVIEW = 10;
  const shown = open ? files : files.slice(0, PREVIEW);
  const hidden = files.length - shown.length;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Files className="h-4 w-4 text-safer-ice" />
          Scanned files ({files.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1">
        <ul className="text-[11px] font-mono text-muted-foreground space-y-0.5 max-h-64 overflow-auto">
          {shown.map((path) => (
            <li key={path} className="break-all">
              {path}
            </li>
          ))}
        </ul>
        {files.length > PREVIEW && (
          <button
            onClick={() => setOpen((o) => !o)}
            className="inline-flex items-center gap-1 text-[11px] text-safer-ice hover:underline font-mono"
          >
            {open ? (
              <>
                <ChevronDown className="h-3 w-3" /> collapse
              </>
            ) : (
              <>
                <ChevronRight className="h-3 w-3" /> show all ({hidden} more)
              </>
            )}
          </button>
        )}
      </CardContent>
    </Card>
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
          <dt className="text-muted-foreground">scan_mode</dt>
          <dd>{report.scan_mode}</dd>
          <dt className="text-muted-foreground">scanned files</dt>
          <dd>{report.scanned_files.length}</dd>
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
          <FileCode className="h-4 w-4 text-safer-ice" />
          AST summary
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {summary.parse_error ? (
          <p className="text-xs text-safer-critical font-mono break-all">
            parse error: {summary.parse_error}
          </p>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
              <dt className="text-muted-foreground">module</dt>
              <dd className="break-all">{summary.module || "—"}</dd>
              <dt className="text-muted-foreground">lines</dt>
              <dd>{summary.loc}</dd>
              <dt className="text-muted-foreground">entry points</dt>
              <dd className="break-all">{summary.entry_points.join(", ") || "—"}</dd>
              <dt className="text-muted-foreground">imports</dt>
              <dd className="break-all">{summary.imports.join(", ") || "—"}</dd>
            </div>
            {summary.tools.length > 0 && (
              <div>
                <div className="text-[11px] uppercase text-muted-foreground mb-1 flex items-center gap-1">
                  <Wrench className="h-3 w-3" /> tools ({summary.tools.length})
                </div>
                <div className="space-y-1.5">
                  {summary.tools.map((t, i) => (
                    <div
                      key={`${t.file_path ?? ""}:${t.name}:${i}`}
                      className="rounded-md border border-border bg-card/40 p-2 text-xs"
                    >
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant={severityVariant[t.risk_class]}>
                          {t.risk_class}
                        </Badge>
                        <span className="font-mono break-all">{t.signature}</span>
                        {t.file_path && (
                          <span className="text-[11px] text-muted-foreground font-mono ml-auto">
                            {t.file_path}
                          </span>
                        )}
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
                        {c.file_path ? `${c.file_path}:${c.line}` : `line ${c.line}`}
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
        <CardTitle className="text-base">
          Pattern matches ({matches.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5">
        {matches.map((m, i) => (
          <div
            key={i}
            className="rounded-md border border-border bg-card/40 p-2 text-xs font-mono"
          >
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant={severityVariant[m.severity]}>{m.severity}</Badge>
              <Badge variant="outline">{m.rule_id}</Badge>
              <Badge variant="outline">{m.flag}</Badge>
              <span className="text-muted-foreground ml-auto">
                {m.file_path ? `${m.file_path}:${m.line}` : `line ${m.line}`}
              </span>
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

function FindingsByPersona({ findings }: { findings: Finding[] }) {
  // Group persona-sourced findings by their persona key.
  const byPersona = useMemo(() => {
    const map: Record<string, Finding[]> = {};
    for (const p of PERSONAS) map[p.key] = [];
    for (const f of findings) {
      if (typeof f.persona === "string" && f.persona in map) {
        map[f.persona].push(f);
      }
    }
    return map;
  }, [findings]);

  const anyPersonaHasIssue = Object.values(byPersona).some((list) =>
    list.some((f) => f.severity === "CRITICAL" || f.severity === "HIGH")
  );

  return (
    <div className="space-y-2">
      {!anyPersonaHasIssue && (
        <p className="text-xs text-muted-foreground font-mono px-1">
          Low/medium findings are collapsed by default — click a persona row
          to expand.
        </p>
      )}
      {PERSONAS.map(({ key, label }) => (
        <PersonaAccordion
          key={key}
          label={label}
          findings={byPersona[key] ?? []}
        />
      ))}
    </div>
  );
}

function PersonaAccordion({
  label,
  findings,
}: {
  label: string;
  findings: Finding[];
}) {
  const isEmpty = findings.length === 0;
  const worstSeverity = findings.reduce<
    "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | null
  >((acc, f) => {
    const order = { LOW: 1, MEDIUM: 2, HIGH: 3, CRITICAL: 4 } as const;
    if (!acc) return f.severity;
    return order[f.severity] > order[acc] ? f.severity : acc;
  }, null);
  const defaultOpen =
    !isEmpty && (worstSeverity === "CRITICAL" || worstSeverity === "HIGH");
  const [open, setOpen] = useState(defaultOpen);

  if (isEmpty) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 rounded-md border px-3 py-2 text-xs font-mono",
          "border-safer-success/30 bg-safer-success/10 text-safer-success"
        )}
      >
        <CheckCircle2 className="h-4 w-4 shrink-0" />
        <span>No findings from {label}</span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "rounded-md border overflow-hidden",
        worstSeverity === "CRITICAL"
          ? "border-safer-critical/40"
          : worstSeverity === "HIGH"
          ? "border-safer-warning/40"
          : "border-border"
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs font-mono hover:bg-muted/30 transition"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        )}
        <span className="font-semibold">{label}</span>
        <Badge
          variant={worstSeverity ? severityVariant[worstSeverity] : "outline"}
        >
          {findings.length} finding{findings.length === 1 ? "" : "s"}
        </Badge>
      </button>
      {open && (
        <div className="border-t border-border/60 bg-card/40 p-3 space-y-1.5 animate-fadein">
          {findings.map((f) => (
            <div
              key={f.finding_id}
              className={cn(
                "rounded-md border p-2 text-xs",
                f.severity === "CRITICAL"
                  ? "border-safer-critical/40 bg-safer-critical/5"
                  : "border-border bg-card/60"
              )}
            >
              <div className="flex items-center gap-2 flex-wrap font-mono">
                <Badge variant={severityVariant[f.severity]}>{f.severity}</Badge>
                <Badge variant="outline">{f.flag}</Badge>
                <Badge variant="outline">{f.category}</Badge>
                <span className="break-all">{f.title}</span>
                {f.file_path && (
                  <span className="text-[11px] text-muted-foreground ml-auto">
                    {f.file_path}
                  </span>
                )}
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {f.description}
              </p>
              {f.evidence.length > 0 && (
                <ul className="mt-1 text-[11px] font-mono text-muted-foreground space-y-0.5">
                  {f.evidence.slice(0, 3).map((e, i) => (
                    <li key={i} className="break-all">
                      · {e}
                    </li>
                  ))}
                </ul>
              )}
              {f.recommended_mitigation && (
                <p className="mt-1 text-[11px] font-mono">
                  <span className="text-muted-foreground">mitigation: </span>
                  {f.recommended_mitigation}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SuggestionsCard({ suggestions }: { suggestions: PolicySuggestion[] }) {
  if (suggestions.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Policy suggestions</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground font-mono">
            No policy suggestions — Inspector did not recommend new guard
            rules for this scan.
          </p>
        </CardContent>
      </Card>
    );
  }
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
