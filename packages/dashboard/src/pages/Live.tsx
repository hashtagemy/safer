import { useSaferRealtime, SaferEvent } from "@/lib/ws";
import { Card, CardContent } from "@/components/ui/Card";
import { HookBadge, RiskBadge } from "@/components/ui/Badge";
import { useState } from "react";
import { cn } from "@/lib/utils";

export default function Live() {
  const { events, connected } = useSaferRealtime(500);
  const [selected, setSelected] = useState<SaferEvent | null>(null);

  const reversed = [...events].reverse();

  return (
    <div className="flex h-full">
      <div className="flex-1 p-6 overflow-hidden flex flex-col">
        <div className="flex items-baseline justify-between mb-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Live events</h1>
            <p className="text-sm text-muted-foreground">
              {connected ? "Streaming in real time." : "Stream disconnected — retrying…"}
            </p>
          </div>
          <span className="text-sm text-muted-foreground font-mono">
            {events.length} events
          </span>
        </div>

        <Card className="flex-1 overflow-hidden">
          <CardContent className="p-0 h-full overflow-auto">
            {reversed.length === 0 ? (
              <div className="p-8 text-center text-sm text-muted-foreground font-mono">
                Waiting for events. Run an instrumented agent to see activity.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-card border-b border-border">
                  <tr className="text-left text-xs text-muted-foreground font-normal">
                    <th className="p-3 w-28">time</th>
                    <th className="p-3 w-36">hook</th>
                    <th className="p-3 w-20">risk</th>
                    <th className="p-3">agent / session</th>
                    <th className="p-3 w-16 text-right">#</th>
                  </tr>
                </thead>
                <tbody>
                  {reversed.map((ev) => (
                    <tr
                      key={ev.event_id}
                      onClick={() => setSelected(ev)}
                      className={cn(
                        "border-b border-border/40 hover:bg-muted/40 cursor-pointer font-mono",
                        ev.risk_hint === "CRITICAL" && "bg-safer-critical/5 animate-pulse-critical",
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
                      <td className="p-3 text-muted-foreground truncate">
                        {ev.agent_id} · {ev.session_id}
                      </td>
                      <td className="p-3 text-right text-xs text-muted-foreground">
                        {ev.sequence}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      </div>

      {selected && (
        <aside className="w-[420px] shrink-0 border-l border-border bg-card/40 flex flex-col animate-fadein">
          <div className="p-4 border-b border-border flex items-center justify-between">
            <div className="flex items-center gap-2">
              <HookBadge hook={selected.hook} />
              <RiskBadge risk={selected.risk_hint} />
            </div>
            <button
              onClick={() => setSelected(null)}
              className="text-muted-foreground text-xs hover:text-foreground"
            >
              close (esc)
            </button>
          </div>
          <div className="p-4 space-y-4 overflow-auto text-sm">
            <section>
              <div className="text-xs text-muted-foreground mb-1">meta</div>
              <dl className="text-xs font-mono space-y-0.5">
                <Row k="event_id" v={selected.event_id} />
                <Row k="session_id" v={selected.session_id} />
                <Row k="agent_id" v={selected.agent_id} />
                <Row k="sequence" v={String(selected.sequence)} />
                <Row k="timestamp" v={selected.timestamp} />
              </dl>
            </section>
            <section>
              <div className="text-xs text-muted-foreground mb-1">
                persona verdicts
              </div>
              <div className="text-xs text-muted-foreground italic">
                Judge not yet active in Phase 3 — enables in Phase 6.
              </div>
            </section>
            <section>
              <div className="text-xs text-muted-foreground mb-1">payload</div>
              <pre className="text-xs font-mono bg-muted/40 rounded-md p-3 overflow-auto">
                {JSON.stringify(selected.payload, null, 2)}
              </pre>
            </section>
          </div>
        </aside>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-start gap-2">
      <dt className="text-muted-foreground w-24 shrink-0">{k}</dt>
      <dd className="break-all">{v}</dd>
    </div>
  );
}
