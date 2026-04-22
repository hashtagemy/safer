import { useSaferRealtime } from "@/lib/ws";
import { cn } from "@/lib/utils";

const MAX_DOTS = 24;

/**
 * Narrow horizontal strip showing the last N events as colored dots.
 * Gives a subliminal "system is alive" feeling. Color-codes by hook.
 */
export default function HeartbeatStrip() {
  const { events } = useSaferRealtime(MAX_DOTS);

  const recent = events.slice(-MAX_DOTS);

  return (
    <div className="relative h-6 w-full border border-border rounded-full bg-card/40 overflow-hidden">
      <div className="absolute inset-0 flex items-center gap-1 px-2 overflow-hidden">
        {recent.map((ev) => (
          <div
            key={ev.event_id}
            className={cn(
              "h-2 w-2 rounded-full shrink-0 animate-fadein",
              hookColor(ev.hook, ev.risk_hint)
            )}
            title={`${ev.hook} · ${ev.agent_id}`}
          />
        ))}
        {recent.length === 0 && (
          <span className="text-[10px] text-muted-foreground font-mono mx-auto">
            waiting for events…
          </span>
        )}
      </div>
    </div>
  );
}

function hookColor(hook: string, risk: string): string {
  if (risk === "CRITICAL") return "bg-safer-critical animate-pulse-critical";
  if (risk === "HIGH") return "bg-safer-warning";
  if (risk === "MEDIUM") return "bg-safer-iceDeep";
  if (hook === "before_tool_use" || hook === "after_tool_use") return "bg-safer-ice";
  if (hook === "on_final_output") return "bg-safer-success";
  if (hook === "on_error") return "bg-safer-critical";
  return "bg-muted-foreground/40";
}
