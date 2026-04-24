import { forwardRef } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { HookBadge, RiskBadge } from "@/components/ui/Badge";
import { EventDetailPanel } from "@/components/EventDetailPanel";
import { cn } from "@/lib/utils";
import type { SessionEvent } from "@/lib/sessionTypes";
import type { VerdictMsg, PreStepScoreMsg, SaferEvent } from "@/lib/ws";

function eventSummary(ev: SessionEvent): string {
  const p = ev.payload as Record<string, unknown>;
  if (ev.hook === "before_tool_use" || ev.hook === "after_tool_use") {
    const tool = (p.tool_name as string) || "tool";
    return `${tool}()`;
  }
  if (ev.hook === "before_llm_call" || ev.hook === "after_llm_call") {
    const model = (p.model as string) || "claude";
    return `LLM call · ${model}`;
  }
  if (ev.hook === "on_final_output") {
    const r = (p.final_response as string) || "";
    return r.slice(0, 120) || "final output";
  }
  if (ev.hook === "on_agent_decision") {
    return (p.reasoning as string)?.slice(0, 120) || "decision";
  }
  if (ev.hook === "on_session_start") return "session started";
  if (ev.hook === "on_session_end") return "session ended";
  if (ev.hook === "on_error") {
    return (p.message as string)?.slice(0, 120) || "error";
  }
  return ev.hook;
}

function toSaferEvent(ev: SessionEvent): SaferEvent {
  return {
    type: "event",
    event_id: ev.event_id,
    session_id: ev.session_id,
    agent_id: ev.agent_id,
    hook: ev.hook,
    sequence: ev.sequence,
    timestamp: ev.timestamp,
    risk_hint: ev.risk_hint,
    payload: ev.payload,
  };
}

export interface TimelineProps {
  events: SessionEvent[];
  expandedIds: Set<string>;
  onToggle: (ev: SessionEvent) => void;
  verdictsByEventId: Record<string, VerdictMsg>;
  prestepByEventId: Record<string, PreStepScoreMsg>;
  /** Registers a DOM node for a given event_id so parents can scrollIntoView. */
  registerCardRef?: (eventId: string, el: HTMLLIElement | null) => void;
}

export function Timeline({
  events,
  expandedIds,
  onToggle,
  verdictsByEventId,
  prestepByEventId,
  registerCardRef,
}: TimelineProps) {
  if (events.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground font-mono">
            No events persisted for this session.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Timeline</CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          {events.length} events. Click a card to expand its detail below;
          click again to collapse. Multiple can stay open for side-by-side
          comparison. Esc closes all.
        </p>
      </CardHeader>
      <CardContent>
        <ol className="relative border-l border-border ml-3 space-y-3 py-1">
          {events.map((ev) => (
            <TimelineCard
              key={ev.event_id}
              ev={ev}
              expanded={expandedIds.has(ev.event_id)}
              onToggle={onToggle}
              verdict={verdictsByEventId[ev.event_id]}
              prestep={prestepByEventId[ev.event_id]}
              registerRef={registerCardRef}
            />
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

interface TimelineCardProps {
  ev: SessionEvent;
  expanded: boolean;
  onToggle: (ev: SessionEvent) => void;
  verdict: VerdictMsg | undefined;
  prestep: PreStepScoreMsg | undefined;
  registerRef?: (eventId: string, el: HTMLLIElement | null) => void;
}

const TimelineCard = forwardRef<HTMLLIElement, TimelineCardProps>(
  function TimelineCard(
    { ev, expanded, onToggle, verdict, prestep, registerRef },
    _ref
  ) {
    return (
      <li
        ref={(el) => registerRef?.(ev.event_id, el)}
        className="ml-4"
      >
        <span
          className={cn(
            "absolute -left-1.5 mt-1.5 w-3 h-3 rounded-full border border-border",
            ev.risk_hint === "CRITICAL"
              ? "bg-safer-critical animate-pulse"
              : ev.risk_hint === "HIGH"
              ? "bg-safer-warning"
              : ev.risk_hint === "MEDIUM"
              ? "bg-safer-ice"
              : "bg-safer-success"
          )}
        />
        <button
          onClick={() => onToggle(ev)}
          aria-expanded={expanded}
          className={cn(
            "w-full text-left rounded-md border px-3 py-2 transition",
            expanded
              ? "border-safer-ice/50 bg-safer-ice/5"
              : "border-border bg-card/40 hover:bg-muted/40"
          )}
        >
          <div className="flex items-center gap-2 text-xs font-mono">
            <span className="text-muted-foreground w-8 text-right">
              {ev.sequence}
            </span>
            <HookBadge hook={ev.hook} />
            <RiskBadge risk={ev.risk_hint} />
            <span className="text-muted-foreground ml-auto text-[11px]">
              {new Date(ev.timestamp).toLocaleTimeString()}
            </span>
          </div>
          <p className="mt-1 text-xs text-foreground/90 font-mono break-all line-clamp-1">
            {eventSummary(ev)}
          </p>
        </button>
        {expanded && (
          <EventDetailPanel
            event={toSaferEvent(ev)}
            verdict={verdict}
            prestep={prestep}
            onClose={() => onToggle(ev)}
          />
        )}
      </li>
    );
  }
);
