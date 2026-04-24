import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { SessionReportCard, ReportLoadError } from "@/components/SessionReport/Card";
import { NarrativeStreaming } from "@/components/NarrativeStreaming";
import { Timeline } from "@/components/Timeline";
import { TraceTree } from "@/components/TraceTree";
import { BACKEND_URL, fetchJSON } from "@/lib/api";
import { useSaferRealtime } from "@/lib/ws";
import type { SessionEvent, SessionReport } from "@/lib/sessionTypes";

interface EventsResponse {
  session_id: string;
  events: SessionEvent[];
}

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const sessionId = id ?? "";

  const { verdictsByEventId, prestepByEventId } = useSaferRealtime(500);

  const [report, setReport] = useState<SessionReport | null>(null);
  const [events, setEvents] = useState<SessionEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [reconstructing, setReconstructing] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const cardRefs = useRef<Record<string, HTMLLIElement | null>>({});
  const registerCardRef = useCallback(
    (eventId: string, el: HTMLLIElement | null) => {
      if (el) cardRefs.current[eventId] = el;
      else delete cardRefs.current[eventId];
    },
    []
  );

  const toggleEvent = useCallback((ev: SessionEvent) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(ev.event_id)) next.delete(ev.event_id);
      else next.add(ev.event_id);
      return next;
    });
  }, []);

  const expandFromTree = useCallback((ev: SessionEvent) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.add(ev.event_id);
      return next;
    });
    // Defer scroll until the card has rendered its expanded panel.
    requestAnimationFrame(() => {
      const el = cardRefs.current[ev.event_id];
      el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setExpandedIds((prev) => (prev.size === 0 ? prev : new Set()));
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const loadReport = useCallback(async () => {
    if (!sessionId) return;
    setError(null);
    try {
      const r = await fetchJSON<SessionReport>(
        `/v1/sessions/${encodeURIComponent(sessionId)}/report`
      );
      setReport(r);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [sessionId]);

  const loadEvents = useCallback(async () => {
    if (!sessionId) return;
    try {
      const r = await fetchJSON<EventsResponse>(
        `/v1/sessions/${encodeURIComponent(sessionId)}/events`
      );
      setEvents(r.events);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [sessionId]);

  useEffect(() => {
    loadReport();
    loadEvents();
  }, [loadReport, loadEvents]);

  const regenerate = async () => {
    setRegenerating(true);
    try {
      const r = await fetch(
        `${BACKEND_URL}/v1/sessions/${encodeURIComponent(sessionId)}/report/generate`,
        { method: "POST" }
      );
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      setReport((await r.json()) as SessionReport);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRegenerating(false);
    }
  };

  const reconstruct = async () => {
    setReconstructing(true);
    try {
      const r = await fetch(
        `${BACKEND_URL}/v1/sessions/${encodeURIComponent(sessionId)}/report/generate?force_reconstruct=true`,
        { method: "POST" }
      );
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      setReport((await r.json()) as SessionReport);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setReconstructing(false);
    }
  };

  return (
    <div className="p-6 overflow-auto space-y-4 h-full">
      <div className="flex items-center justify-between">
        <div>
          <Link
            to="/sessions"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground font-mono"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> sessions
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight mt-1">
            Session · {sessionId}
          </h1>
        </div>
      </div>

      {error && !report && <ReportLoadError message={error} />}

      {!report && !error && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground font-mono">
            Loading report…
          </CardContent>
        </Card>
      )}

      {report && (
        <>
          <SessionReportCard
            report={report}
            regenerating={regenerating}
            reconstructing={reconstructing}
            onRegenerate={regenerate}
            onReconstruct={reconstruct}
          />
          <NarrativeStreaming narrative={report.thought_chain_narrative} />
        </>
      )}

      {events && events.length > 0 && (
        <>
          <Timeline
            events={events}
            expandedIds={expandedIds}
            onToggle={toggleEvent}
            verdictsByEventId={verdictsByEventId}
            prestepByEventId={prestepByEventId}
            registerCardRef={registerCardRef}
          />
          <TraceTree events={events} onSelect={expandFromTree} />
        </>
      )}

      {events && events.length === 0 && (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground font-mono">
            No events persisted for this session yet.
          </CardContent>
        </Card>
      )}
    </div>
  );
}
