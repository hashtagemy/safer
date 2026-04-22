import { useEffect, useRef, useState } from "react";
import { WS_URL } from "./api";

export interface SaferEvent {
  type: "event";
  event_id: string;
  session_id: string;
  agent_id: string;
  hook: string;
  sequence: number;
  timestamp: string;
  risk_hint: string;
  payload: Record<string, unknown>;
  gateway?: {
    decision: string;
    risk: string;
    reason: string;
    hits: Array<{
      policy_id: string;
      policy_name: string;
      severity: string;
      flag: string;
      evidence: string[];
    }>;
  };
}

export interface PersonaVerdictMsg {
  persona: string;
  score: number;
  confidence: number;
  flags: string[];
  evidence: string[];
  reasoning: string;
  recommended_mitigation: string | null;
}

export interface VerdictMsg {
  type: "verdict";
  event_id: string;
  session_id: string;
  agent_id: string;
  mode: string;
  overall: {
    risk: string;
    confidence: number;
    block: boolean;
  };
  active_personas: string[];
  personas: Record<string, PersonaVerdictMsg>;
  latency_ms: number;
}

export interface BlockMsg {
  type: "block";
  event_id: string;
  session_id: string;
  agent_id: string;
  source?: "gateway" | "judge";
  reason?: string;
  risk?: string;
  confidence?: number;
  hits?: Array<{ policy_id: string; flag: string }>;
  received_at: number;
}

export interface PreStepScoreMsg {
  type: "prestep_score";
  event_id: string;
  session_id: string;
  agent_id: string;
  relevance_score: number;
  should_escalate: boolean;
  reason: string;
}

export interface SaferStream {
  events: SaferEvent[];
  verdictsByEventId: Record<string, VerdictMsg>;
  prestepByEventId: Record<string, PreStepScoreMsg>;
  blocks: BlockMsg[];
  connected: boolean;
}

/**
 * Subscribes to /ws/stream and keeps the last `limit` events in state.
 * Also tracks verdicts, per-step Haiku scores, and block signals the
 * backend broadcasts.
 *
 * Auto-reconnects with exponential backoff.
 */
export function useSaferRealtime(limit = 500): SaferStream {
  const [events, setEvents] = useState<SaferEvent[]>([]);
  const [verdictsByEventId, setVerdicts] = useState<Record<string, VerdictMsg>>(
    {}
  );
  const [prestepByEventId, setPrestep] = useState<Record<string, PreStepScoreMsg>>(
    {}
  );
  const [blocks, setBlocks] = useState<BlockMsg[]>([]);
  const [connected, setConnected] = useState(false);
  const backoffRef = useRef(500);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let cancelled = false;
    let retryTimer: number | undefined;

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(`${WS_URL}/ws/stream`);
      ws.onopen = () => {
        setConnected(true);
        backoffRef.current = 500;
      };
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          switch (data.type) {
            case "event": {
              const ev = data as SaferEvent;
              setEvents((prev) => {
                const next = [...prev, ev];
                return next.length > limit
                  ? next.slice(next.length - limit)
                  : next;
              });
              break;
            }
            case "verdict": {
              const v = data as VerdictMsg;
              setVerdicts((prev) => ({ ...prev, [v.event_id]: v }));
              break;
            }
            case "prestep_score": {
              const p = data as PreStepScoreMsg;
              setPrestep((prev) => ({ ...prev, [p.event_id]: p }));
              break;
            }
            case "block": {
              const b = { ...(data as BlockMsg), received_at: Date.now() };
              setBlocks((prev) => {
                const next = [...prev, b];
                return next.length > 50 ? next.slice(next.length - 50) : next;
              });
              break;
            }
            default:
              // Unknown message kind — ignore.
              break;
          }
        } catch {
          /* ignore malformed */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) {
          retryTimer = window.setTimeout(connect, backoffRef.current);
          backoffRef.current = Math.min(backoffRef.current * 2, 10_000);
        }
      };
      ws.onerror = () => {
        ws?.close();
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, [limit]);

  return { events, verdictsByEventId, prestepByEventId, blocks, connected };
}
