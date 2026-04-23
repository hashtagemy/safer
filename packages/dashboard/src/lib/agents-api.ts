/**
 * Thin API helpers for the Agent Registry endpoints exposed by the
 * backend (/v1/agents). The shapes mirror the Pydantic models in
 * packages/backend/src/safer_backend/models/agent.py and inspector.py.
 */

import { BACKEND_URL, fetchJSON } from "./api";

export type ScanStatus = "unscanned" | "scanning" | "scanned";

export interface AgentSummary {
  agent_id: string;
  name: string;
  framework: string | null;
  version: string | null;
  created_at: string;
  last_seen_at: string | null;
  risk_score: number;
  latest_scan_id: string | null;
  scan_status: ScanStatus;
  file_count: number;
}

export interface AgentRecord {
  agent_id: string;
  name: string;
  framework: string | null;
  version: string | null;
  system_prompt: string | null;
  project_root: string | null;
  code_snapshot_hash: string | null;
  file_count: number;
  total_bytes: number;
  snapshot_truncated: boolean;
  created_at: string;
  registered_at: string | null;
  last_seen_at: string | null;
  latest_scan_id: string | null;
  risk_score: number;
}

export interface AgentSessionRow {
  session_id: string;
  started_at: string;
  ended_at: string | null;
  total_steps: number;
  total_cost_usd: number;
  success: boolean;
  overall_health: number | null;
  has_report: boolean;
  parent_session_id: string | null;
}

export interface AgentRedTeamRow {
  run_id: string;
  mode: string;
  phase: string;
  started_at: string;
  finished_at: string | null;
  findings_count: number;
  safety_score: number;
}

export interface AgentProfilePatch {
  system_prompt?: string;
  name?: string;
  version?: string;
}

export interface ScanRequest {
  skip_persona_review?: boolean;
  active_policies?: unknown[];
}

export function listAgents(): Promise<AgentSummary[]> {
  return fetchJSON<AgentSummary[]>("/v1/agents");
}

export function getAgent(agentId: string): Promise<AgentRecord> {
  return fetchJSON<AgentRecord>(`/v1/agents/${encodeURIComponent(agentId)}`);
}

export function getAgentSessions(agentId: string): Promise<AgentSessionRow[]> {
  return fetchJSON<AgentSessionRow[]>(
    `/v1/agents/${encodeURIComponent(agentId)}/sessions`
  );
}

export function getAgentRedTeamReports(
  agentId: string
): Promise<AgentRedTeamRow[]> {
  return fetchJSON<AgentRedTeamRow[]>(
    `/v1/agents/${encodeURIComponent(agentId)}/redteam-reports`
  );
}

export async function scanAgent(
  agentId: string,
  request: ScanRequest = {}
): Promise<unknown> {
  const r = await fetch(
    `${BACKEND_URL}/v1/agents/${encodeURIComponent(agentId)}/scan`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }
  );
  if (!r.ok) {
    throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  }
  return r.json();
}

export async function getLatestScan(agentId: string): Promise<unknown | null> {
  const r = await fetch(
    `${BACKEND_URL}/v1/agents/${encodeURIComponent(agentId)}/scan`
  );
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

export async function patchAgentProfile(
  agentId: string,
  patch: AgentProfilePatch
): Promise<AgentRecord> {
  const r = await fetch(
    `${BACKEND_URL}/v1/agents/${encodeURIComponent(agentId)}/profile`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }
  );
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
  return (await r.json()) as AgentRecord;
}
