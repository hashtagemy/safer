import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { SessionReportCard, ReportLoadError } from "@/components/SessionReport/Card";
import { NarrativeStreaming } from "@/components/NarrativeStreaming";
import { Timeline } from "@/components/Timeline";
import { TraceTree } from "@/components/TraceTree";
import { PersonaDrawer } from "@/components/PersonaDrawer";
import { BACKEND_URL, fetchJSON } from "@/lib/api";
import { useSaferRealtime, SaferEvent } from "@/lib/ws";
import type { SessionEvent, SessionReport } from "@/lib/sessionTypes";

interface EventsResponse {
  session_id: string;
  events: SessionEvent[];
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

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const sessionId = id ?? "";

  const { verdictsByEventId, prestepByEventId } = useSaferRealtime(500);

  const [report, setReport] = useState<SessionReport | null>(null);
  const [events, setEvents] = useState<SessionEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [reconstructing, setReconstructing] = useState(false);
  const [selected, setSelected] = useState<SessionEvent | null>(null);

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

  const selectedSaferEvent = selected ? toSaferEvent(selected) : null;

  return (
    <div className="flex h-full">
      <div className="flex-1 p-6 overflow-auto space-y-4">
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
              selectedEventId={selected?.event_id ?? null}
              onSelect={setSelected}
            />
            <TraceTree events={events} onSelect={setSelected} />
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

      <PersonaDrawer
        event={selectedSaferEvent}
        verdict={selected ? verdictsByEventId[selected.event_id] : undefined}
        prestep={selected ? prestepByEventId[selected.event_id] : undefined}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
