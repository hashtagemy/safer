export const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";
export const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8000";

export async function fetchJSON<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BACKEND_URL}${path}`, init);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export interface ActiveSessionRow {
  session_id: string;
  agent_id: string;
  agent_name: string;
  started_at: string;
  total_steps: number;
  last_event_at: string | null;
  last_event_hook: string | null;
  last_risk_hint: string | null;
  recent_hooks: string[];
}

export function listActiveSessions(): Promise<ActiveSessionRow[]> {
  return fetchJSON<ActiveSessionRow[]>("/v1/sessions/active");
}
