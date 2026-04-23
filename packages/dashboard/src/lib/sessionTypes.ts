// Mirrors safer_backend/models/session_report.py and sessions_api.py.

export type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface CategoryScore {
  name: string;
  value: number;
  flag_count_by_severity: Record<string, number>;
}

export interface TimelineEntry {
  step: number;
  hook: string;
  risk: string;
  summary: string;
}

export interface TopFinding {
  severity: Severity;
  category: string;
  flag: string;
  summary: string;
  step: number | null;
}

export interface CostSummary {
  total_usd: number;
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens: number;
  num_opus_calls: number;
  num_haiku_calls: number;
}

export interface RedTeamSummary {
  run_id: string;
  safety_score: number;
  findings_count: number;
  ran_at: string;
}

export interface SessionReport {
  session_id: string;
  agent_id: string;
  agent_name: string;
  generated_at: string;
  overall_health: number;
  categories: CategoryScore[];
  top_findings: TopFinding[];
  owasp_map: Record<string, number>;
  thought_chain_narrative: string | null;
  timeline: TimelineEntry[];
  red_team_summary: RedTeamSummary | null;
  cost: CostSummary;
  duration_ms: number;
  total_steps: number;
  success: boolean;
}

export interface SessionListItem {
  session_id: string;
  agent_id: string;
  agent_name: string;
  started_at: string;
  ended_at: string | null;
  total_steps: number;
  total_cost_usd: number;
  overall_health: number | null;
  success: boolean;
  parent_session_id: string | null;
}

export interface SessionEvent {
  event_id: string;
  session_id: string;
  agent_id: string;
  sequence: number;
  hook: string;
  timestamp: string;
  risk_hint: string;
  source: string;
  payload: Record<string, unknown>;
}
