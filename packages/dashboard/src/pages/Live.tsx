import { useCallback, useEffect, useRef, useState } from "react";
import { Radio } from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { ActiveSessionCard } from "@/components/ActiveSessionCard";
import { BlockMomentToast } from "@/components/BlockMomentToast";
import { listActiveSessions, type ActiveSessionRow, WS_URL } from "@/lib/api";
import { useSaferRealtime, type BlockMsg } from "@/lib/ws";

export default function Live() {
  const [rows, setRows] = useState<ActiveSessionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const { blocks } = useSaferRealtime(200);
  const [toastKey, setToastKey] = useState(0);
  const refetchTimer = useRef<number | undefined>(undefined);

  const refetch = useCallback(async () => {
    try {
      const data = await listActiveSessions();
      setRows(data);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  // WebSocket listener that mutates the card list in-place for live
  // feedback, then falls back to a debounced refetch so the backend
  // stays authoritative on fields we can't compute client-side.
  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;

    const scheduleRefetch = () => {
      if (refetchTimer.current) window.clearTimeout(refetchTimer.current);
      refetchTimer.current = window.setTimeout(() => {
        if (!cancelled) refetch();
      }, 200);
    };

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(`${WS_URL}/ws/stream`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (data?.type !== "event") return;
          const hook = data.hook as string;
          const sessionId = data.session_id as string;
          const agentId = data.agent_id as string;
          if (hook === "on_agent_register") return; // not a runtime event

          if (hook === "on_session_start" || hook === "on_session_end") {
            scheduleRefetch();
            return;
          }

          // Runtime event — mutate or insert the row so the card feels
          // instant even before the refetch round-trip completes.
          setRows((prev) => {
            if (prev === null) return prev;
            const idx = prev.findIndex((r) => r.session_id === sessionId);
            if (idx === -1) {
              // Unknown session — trigger a refetch so we get the full row.
              scheduleRefetch();
              return prev;
            }
            const row = prev[idx];
            const nextRecent = [...row.recent_hooks, hook].slice(-20);
            const updated: ActiveSessionRow = {
              ...row,
              last_event_at: data.timestamp,
              last_event_hook: hook,
              last_risk_hint: data.risk_hint ?? row.last_risk_hint,
              total_steps: row.total_steps + 1,
              recent_hooks: nextRecent,
            };
            // Pull the updated row to the top — newest activity first.
            const without = prev.slice(0, idx).concat(prev.slice(idx + 1));
            void agentId;
            return [updated, ...without];
          });
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) window.setTimeout(connect, 2000);
      };
      ws.onerror = () => ws?.close();
    };
    connect();

    return () => {
      cancelled = true;
      if (refetchTimer.current) window.clearTimeout(refetchTimer.current);
      ws?.close();
    };
  }, [refetch]);

  const latestBlock: BlockMsg | null =
    blocks.length === 0 ? null : blocks[blocks.length - 1];
  const activeToast =
    latestBlock && latestBlock.received_at >= toastKey ? latestBlock : null;

  return (
    <div className="p-6 space-y-4">
      <header className="flex items-baseline justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Live sessions
          </h1>
          <p className="text-sm text-muted-foreground">
            {connected
              ? `Streaming in real time · ${rows?.length ?? 0} active`
              : "Stream disconnected — retrying…"}
          </p>
        </div>
      </header>

      {error && (
        <Card>
          <CardContent className="p-4 text-xs text-safer-critical font-mono">
            {error}
          </CardContent>
        </Card>
      )}

      {rows === null && !error && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground font-mono">
            Loading active sessions…
          </CardContent>
        </Card>
      )}

      {rows !== null && rows.length === 0 && (
        <Card>
          <CardContent className="p-8 text-center space-y-3">
            <Radio className="h-8 w-8 mx-auto text-muted-foreground" />
            <div className="text-sm font-semibold">No active sessions</div>
            <p className="text-xs text-muted-foreground font-mono max-w-md mx-auto">
              Start an instrumented agent to see live activity here. Cards appear
              automatically when a session begins and disappear when it ends.
            </p>
          </CardContent>
        </Card>
      )}

      {rows !== null && rows.length > 0 && (
        <div className="space-y-3">
          {rows.map((r) => (
            <ActiveSessionCard key={r.session_id} row={r} />
          ))}
        </div>
      )}

      <BlockMomentToast
        block={activeToast}
        onExplain={() => {
          if (latestBlock) {
            window.location.href = `/live/${encodeURIComponent(
              latestBlock.session_id
            )}`;
          }
        }}
        onDismiss={() => {
          if (latestBlock) setToastKey(latestBlock.received_at + 1);
        }}
      />
    </div>
  );
}
