import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { Badge, HookBadge, RiskBadge } from "@/components/ui/Badge";
import { PersonaDrawer } from "@/components/PersonaDrawer";
import { BlockMomentToast } from "@/components/BlockMomentToast";
import { fetchJSON } from "@/lib/api";
import { useSaferRealtime, type BlockMsg, type SaferEvent } from "@/lib/ws";
import { cn } from "@/lib/utils";

interface SessionEventsResponse {
  session_id: string;
  events: Array<Omit<SaferEvent, "type" | "gateway">>;
}

export default function LiveSession() {
  const { sessionId: raw } = useParams<{ sessionId: string }>();
  const sessionId = raw ?? "";
  const { events, verdictsByEventId, prestepByEventId, blocks, connected } =
    useSaferRealtime(1000);

  const [selected, setSelected] = useState<SaferEvent | null>(null);
  const [toastKey, setToastKey] = useState(0);
  const [historical, setHistorical] = useState<SaferEvent[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  // Fetch any events that landed before this page mounted (the
  // websocket buffer only carries events delivered after subscribe).
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    setHistoryLoaded(false);
    setHistorical([]);
    fetchJSON<SessionEventsResponse>(
      `/v1/sessions/${encodeURIComponent(sessionId)}/events`
    )
      .then((res) => {
        if (cancelled) return;
        const past: SaferEvent[] = res.events.map((e) => ({
          ...e,
          type: "event" as const,
        }));
        setHistorical(past);
        setHistoryLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setHistoryLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const sessionEvents = useMemo(() => {
    const live = events.filter((e) => e.session_id === sessionId);
    const seen = new Set(live.map((e) => e.event_id));
    const merged = [
      ...historical.filter((e) => !seen.has(e.event_id)),
      ...live,
    ];
    merged.sort((a, b) => a.sequence - b.sequence);
    return merged;
  }, [events, historical, sessionId]);
  const reversed = useMemo(
    () => [...sessionEvents].reverse(),
    [sessionEvents]
  );

  // Session metadata we can infer from the stream — agent, step count,
  // whether we've seen on_session_end so we can surface an "ended" banner.
  const agentId = sessionEvents[0]?.agent_id;
  const ended = sessionEvents.some((e) => e.hook === "on_session_end");

  const sessionBlocks = useMemo(
    () => blocks.filter((b) => b.session_id === sessionId),
    [blocks, sessionId]
  );
  const latestBlock: BlockMsg | null =
    sessionBlocks.length === 0 ? null : sessionBlocks[sessionBlocks.length - 1];
  const activeToast =
    latestBlock && latestBlock.received_at >= toastKey ? latestBlock : null;

  const explainToast = (b: BlockMsg) => {
    const match = events.find((e) => e.event_id === b.event_id);
    if (match) setSelected(match);
    if (latestBlock) setToastKey(latestBlock.received_at + 1);
  };

  return (
    <div className="flex h-full">
      <div className="flex-1 p-6 overflow-hidden flex flex-col">
        <Link
          to="/live"
          className="inline-flex items-center gap-2 text-xs text-muted-foreground font-mono hover:text-foreground mb-3 self-start"
        >
          <ArrowLeft className="h-3 w-3" /> Back to live
        </Link>

        <div className="flex items-baseline justify-between mb-4 gap-4 flex-wrap">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight break-all">
              {sessionId}
            </h1>
            <div className="text-xs text-muted-foreground font-mono flex items-center gap-2 mt-1 flex-wrap">
              {agentId && (
                <Link
                  to={`/agents/${encodeURIComponent(agentId)}`}
                  className="text-safer-ice hover:underline"
                >
                  {agentId}
                </Link>
              )}
              <span>· {sessionEvents.length} events</span>
              {ended && <Badge variant="muted">session ended</Badge>}
              <span className="text-muted-foreground">
                · {connected ? "streaming" : "disconnected"}
              </span>
            </div>
          </div>
        </div>

        <Card className="flex-1 overflow-hidden">
          <CardContent className="p-0 h-full overflow-auto">
            {!historyLoaded ? (
              <div className="p-8 text-center text-sm text-muted-foreground font-mono">
                Loading session events…
              </div>
            ) : reversed.length === 0 ? (
              <div className="p-8 text-center text-sm text-muted-foreground font-mono">
                Waiting for events on this session…
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-card border-b border-border">
                  <tr className="text-left text-xs text-muted-foreground font-normal">
                    <th className="p-3 w-28">time</th>
                    <th className="p-3 w-36">hook</th>
                    <th className="p-3 w-20">risk</th>
                    <th className="p-3">payload</th>
                    <th className="p-3 w-24">signals</th>
                    <th className="p-3 w-16 text-right">#</th>
                  </tr>
                </thead>
                <tbody>
                  {reversed.map((ev) => {
                    const verdict = verdictsByEventId[ev.event_id];
                    const block =
                      verdict?.overall.block ||
                      ev.gateway?.decision === "block";
                    const summary = summarizePayload(ev);
                    return (
                      <tr
                        key={ev.event_id}
                        onClick={() => setSelected(ev)}
                        className={cn(
                          "border-b border-border/40 hover:bg-muted/40 cursor-pointer font-mono",
                          ev.risk_hint === "CRITICAL" &&
                            "bg-safer-critical/5 animate-pulse-critical",
                          selected?.event_id === ev.event_id && "bg-muted/60"
                        )}
                      >
                        <td className="p-3 text-muted-foreground text-xs">
                          {new Date(ev.timestamp).toLocaleTimeString()}
                        </td>
                        <td className="p-3">
                          <HookBadge hook={ev.hook} />
                        </td>
                        <td className="p-3">
                          <RiskBadge risk={ev.risk_hint} />
                        </td>
                        <td className="p-3 text-xs text-muted-foreground truncate max-w-0">
                          {summary}
                        </td>
                        <td className="p-3">
                          <div className="flex items-center gap-1 flex-wrap">
                            {verdict && (
                              <Badge variant="ice">
                                J{verdict.active_personas.length}
                              </Badge>
                            )}
                            {block && <Badge variant="critical">BLOCK</Badge>}
                          </div>
                        </td>
                        <td className="p-3 text-right text-xs text-muted-foreground">
                          {ev.sequence}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>

      <PersonaDrawer
        event={selected}
        verdict={selected ? verdictsByEventId[selected.event_id] : undefined}
        prestep={selected ? prestepByEventId[selected.event_id] : undefined}
        onClose={() => setSelected(null)}
      />

      <BlockMomentToast
        block={activeToast}
        onExplain={explainToast}
        onDismiss={() => {
          if (latestBlock) setToastKey(latestBlock.received_at + 1);
        }}
      />
    </div>
  );
}

function summarizePayload(ev: SaferEvent): string {
  const p = ev.payload as Record<string, unknown>;
  switch (ev.hook) {
    case "before_tool_use":
    case "after_tool_use":
      return String(p.tool_name ?? "");
    case "before_llm_call":
    case "after_llm_call":
      return String(p.model ?? "");
    case "on_agent_decision":
      return String(p.chosen_action ?? p.decision_type ?? "");
    case "on_final_output": {
      const s = String(p.final_response ?? "");
      return s.length > 120 ? s.slice(0, 117) + "…" : s;
    }
    case "on_error":
      return `${p.error_type ?? ""}: ${p.message ?? ""}`;
    default:
      return "";
  }
}
