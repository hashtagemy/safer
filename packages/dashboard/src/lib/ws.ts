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
}

/**
 * Subscribes to /ws/stream and keeps the last `limit` events in state.
 * Auto-reconnects with exponential backoff.
 */
export function useSaferRealtime(limit = 500): {
  events: SaferEvent[];
  connected: boolean;
} {
  const [events, setEvents] = useState<SaferEvent[]>([]);
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
          if (data.type === "event") {
            setEvents((prev) => {
              const next = [...prev, data as SaferEvent];
              return next.length > limit ? next.slice(next.length - limit) : next;
            });
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

  return { events, connected };
}
