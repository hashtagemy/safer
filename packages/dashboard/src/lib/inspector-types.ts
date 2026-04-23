/**
 * Shared type definitions for Inspector reports returned by
 * POST /v1/agents/{id}/scan and GET /v1/agents/{id}/scan.
 * Mirrors packages/backend/src/safer_backend/models/inspector.py.
 */

export type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type RiskClass = Severity;

export interface ToolSpec {
  name: string;
  signature: string;
  docstring: string | null;
  decorators: string[];
  risk_class: RiskClass;
  risk_reason: string;
  file_path: string | null;
}

export interface LLMCallSite {
  provider: string;
  function: string;
  line: number;
  file_path: string | null;
}

export interface ASTSummary {
  module: string;
  tools: ToolSpec[];
  llm_calls: LLMCallSite[];
  entry_points: string[];
  imports: string[];
  loc: number;
  parse_error: string | null;
}

export interface PatternMatch {
  rule_id: string;
  severity: Severity;
  flag: string;
  line: number;
  snippet: string;
  message: string;
  file_path: string | null;
}

export interface Finding {
  finding_id: string;
  severity: Severity;
  category: string;
  flag: string;
  title: string;
  description: string;
  evidence: string[];
  reproduction_steps: string[];
  recommended_mitigation: string | null;
  file_path: string | null;
}

export interface PolicySuggestion {
  suggestion_id: string;
  name: string;
  reason: string;
  natural_language: string;
  triggering_flags: string[];
  severity: Severity;
}

export interface InspectorReport {
  report_id: string;
  agent_id: string;
  created_at: string;
  risk_score: number;
  risk_level: Severity;
  ast_summary: ASTSummary;
  pattern_matches: PatternMatch[];
  findings: Finding[];
  policy_suggestions: PolicySuggestion[];
  scan_mode: "single" | "project";
  scanned_files: string[];
  duration_ms: number;
  persona_review_skipped: boolean;
  persona_review_error: string | null;
}

export type SeverityVariant =
  | "ice"
  | "success"
  | "warning"
  | "critical"
  | "muted"
  | "outline";

export const severityVariant: Record<Severity, SeverityVariant> = {
  LOW: "success",
  MEDIUM: "ice",
  HIGH: "warning",
  CRITICAL: "critical",
};
