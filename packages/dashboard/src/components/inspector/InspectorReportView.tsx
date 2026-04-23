import {
  CheckCircle2,
  FileCode,
  MessageSquare,
  Wrench,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Gauge } from "@/components/SessionReport/Gauge";
import { cn } from "@/lib/utils";
import {
  ASTSummary,
  Finding,
  InspectorReport,
  PatternMatch,
  PolicySuggestion,
  severityVariant,
} from "@/lib/inspector-types";

export function InspectorReportView({ report }: { report: InspectorReport }) {
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
              {f.file_path && (
                <span className="text-[11px] text-muted-foreground ml-auto">
                  {f.file_path}
                </span>
              )}
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
