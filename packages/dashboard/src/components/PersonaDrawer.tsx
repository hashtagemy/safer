import { useEffect } from "react";
import { X } from "lucide-react";
import { HookBadge, RiskBadge, Badge } from "@/components/ui/Badge";
import { EventDetailPanel } from "@/components/EventDetailPanel";
import { SaferEvent, VerdictMsg, PreStepScoreMsg } from "@/lib/ws";

export interface PersonaDrawerProps {
  event: SaferEvent | null;
  verdict: VerdictMsg | undefined;
  prestep: PreStepScoreMsg | undefined;
  onClose: () => void;
}

/**
 * Side-drawer wrapper used by table-based pages (LiveSession) where
 * inline-below-the-row expansion would break the grid. For list/card-based
 * layouts (SessionDetail timeline), prefer inline <EventDetailPanel> right
 * under the selected card instead of this drawer.
 */
export function PersonaDrawer({
  event,
  verdict,
  prestep,
  onClose,
}: PersonaDrawerProps) {
  useEffect(() => {
    if (!event) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [event, onClose]);

  if (!event) return null;

  return (
    <aside className="w-[460px] shrink-0 border-l border-border bg-card/40 flex flex-col animate-fadein">
      <div className="p-4 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2 flex-wrap">
          <HookBadge hook={event.hook} />
          <RiskBadge risk={event.risk_hint} />
          {verdict?.overall.block && <Badge variant="critical">BLOCK</Badge>}
        </div>
        <button
          onClick={onClose}
          className="text-muted-foreground text-xs hover:text-foreground inline-flex items-center gap-1"
          title="Close (Esc)"
        >
          <X className="h-3.5 w-3.5" /> esc
        </button>
      </div>

      <div className="overflow-auto">
        <EventDetailPanel
          event={event}
          verdict={verdict}
          prestep={prestep}
          variant="bare"
        />
      </div>
    </aside>
  );
}
