import { useMemo, useState } from "react";
import { useSaferRealtime, SaferEvent, BlockMsg } from "@/lib/ws";
import { Card, CardContent } from "@/components/ui/Card";
import { Badge, HookBadge, RiskBadge } from "@/components/ui/Badge";
import { PersonaDrawer } from "@/components/PersonaDrawer";
import { BlockMomentToast } from "@/components/BlockMomentToast";
import { cn } from "@/lib/utils";

const ALL_HOOKS: string[] = [
  "on_session_start",
  "before_llm_call",
  "after_llm_call",
  "before_tool_use",
  "after_tool_use",
  "on_agent_decision",
  "on_final_output",
  "on_session_end",
  "on_error",
];

const ALL_RISKS: string[] = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

export default function Live() {
  const { events, verdictsByEventId, prestepByEventId, blocks, connected } =
    useSaferRealtime(500);

  const [selected, setSelected] = useState<SaferEvent | null>(null);
  const [filterAgent, setFilterAgent] = useState<string>("");
  const [filterHook, setFilterHook] = useState<string>("");
  const [filterRisk, setFilterRisk] = useState<string>("");
  const [toastKey, setToastKey] = useState(0);

  const uniqueAgents = useMemo(() => {
    const s = new Set<string>();
    events.forEach((e) => s.add(e.agent_id));
    return Array.from(s).sort();
  }, [events]);

  const filtered = useMemo(() => {
    return events.filter((e) => {
      if (filterAgent && e.agent_id !== filterAgent) return false;
      if (filterHook && e.hook !== filterHook) return false;
      if (filterRisk && e.risk_hint !== filterRisk) return false;
      return true;
    });
  }, [events, filterAgent, filterHook, filterRisk]);

  const reversed = useMemo(() => [...filtered].reverse(), [filtered]);

  const latestBlock: BlockMsg | null =
    blocks.length === 0 ? null : blocks[blocks.length - 1];
  const activeToast =
    latestBlock && latestBlock.received_at >= toastKey ? latestBlock : null;

  const dismissToast = () => {
    if (latestBlock) setToastKey(latestBlock.received_at + 1);
  };

  const explainToast = (b: BlockMsg) => {
    const match = events.find((e) => e.event_id === b.event_id);
    if (match) setSelected(match);
    dismissToast();
  };

  return (
    <div className="flex h-full">
      <div className="flex-1 p-6 overflow-hidden flex flex-col">
        <div className="flex items-baseline justify-between mb-4 gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Live events</h1>
            <p className="text-sm text-muted-foreground">
              {connected
                ? "Streaming in real time."
                : "Stream disconnected — retrying…"}
            </p>
          </div>
          <div className="flex items-center gap-3 flex-wrap text-xs font-mono">
            <FilterSelect
              label="agent"
              value={filterAgent}
              options={uniqueAgents}
              onChange={setFilterAgent}
            />
            <FilterSelect
              label="hook"
              value={filterHook}
              options={ALL_HOOKS}
              onChange={setFilterHook}
            />
            <FilterSelect
              label="risk"
              value={filterRisk}
              options={ALL_RISKS}
              onChange={setFilterRisk}
            />
            <span className="text-muted-foreground">
              {filtered.length}/{events.length} events
            </span>
          </div>
        </div>

        <Card className="flex-1 overflow-hidden">
          <CardContent className="p-0 h-full overflow-auto">
            {reversed.length === 0 ? (
              <div className="p-8 text-center text-sm text-muted-foreground font-mono">
                {events.length === 0
                  ? "Waiting for events. Run an instrumented agent to see activity."
                  : "No events match the current filters."}
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-card border-b border-border">
                  <tr className="text-left text-xs text-muted-foreground font-normal">
                    <th className="p-3 w-28">time</th>
                    <th className="p-3 w-36">hook</th>
                    <th className="p-3 w-20">risk</th>
                    <th className="p-3">agent / session</th>
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
                        <td className="p-3 text-muted-foreground truncate">
                          {ev.agent_id} · {ev.session_id}
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
        onDismiss={dismissToast}
      />
    </div>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-1">
      <span className="text-muted-foreground">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
      >
        <option value="">all</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}
