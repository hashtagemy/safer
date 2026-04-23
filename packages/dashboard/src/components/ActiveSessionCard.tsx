import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { Badge, HookBadge, RiskBadge } from "@/components/ui/Badge";
import type { ActiveSessionRow } from "@/lib/api";
import { cn } from "@/lib/utils";

// Each hook slot is rendered as a colored square in a horizontal bar
// so operators can scan for "this session just crossed into a tool
// call / decision / error" at a glance.
const HOOK_COLORS: Record<string, string> = {
  on_session_start: "bg-muted",
  before_llm_call: "bg-safer-ice/70",
  after_llm_call: "bg-safer-ice/40",
  before_tool_use: "bg-safer-warning/80",
  after_tool_use: "bg-safer-warning/40",
  on_agent_decision: "bg-safer-success/70",
  on_final_output: "bg-primary/60",
  on_session_end: "bg-muted",
  on_error: "bg-safer-critical/80",
};

const ACTIVITY_SLOT_COUNT = 20;

export function ActiveSessionCard({ row }: { row: ActiveSessionRow }) {
  const critical = row.last_risk_hint === "CRITICAL";
  return (
    <Link
      to={`/live/${encodeURIComponent(row.session_id)}`}
      className="block group"
    >
      <Card
        className={cn(
          "transition group-hover:border-safer-ice/60 group-hover:bg-card/70",
          critical && "border-safer-critical/40 bg-safer-critical/5"
        )}
      >
        <CardContent className="p-4 space-y-3">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold truncate">{row.agent_name}</span>
                <Badge variant="outline" className="font-normal">
                  {row.agent_id}
                </Badge>
              </div>
              <div className="text-[11px] text-muted-foreground font-mono truncate mt-0.5">
                {row.session_id} · started {formatRelative(row.started_at)}
              </div>
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              {row.last_event_hook && <HookBadge hook={row.last_event_hook} />}
              {row.last_risk_hint && <RiskBadge risk={row.last_risk_hint} />}
              <div className="text-xs font-mono text-muted-foreground">
                {row.total_steps} steps
              </div>
              <ChevronRight className="h-4 w-4 text-muted-foreground group-hover:text-safer-ice transition" />
            </div>
          </div>

          <ActivityBar hooks={row.recent_hooks} />
        </CardContent>
      </Card>
    </Link>
  );
}

function ActivityBar({ hooks }: { hooks: string[] }) {
  // Pad with empty slots on the left so bars grow from the left as a
  // session accumulates events — feels more like a heartbeat strip.
  const padded: (string | null)[] = [
    ...Array(Math.max(0, ACTIVITY_SLOT_COUNT - hooks.length)).fill(null),
    ...hooks.slice(-ACTIVITY_SLOT_COUNT),
  ];
  return (
    <div className="flex gap-0.5 h-3 w-full">
      {padded.map((h, i) => (
        <div
          key={i}
          title={h ?? "—"}
          className={cn(
            "flex-1 rounded-sm",
            h ? HOOK_COLORS[h] ?? "bg-primary/30" : "bg-muted/40"
          )}
        />
      ))}
    </div>
  );
}

function formatRelative(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 30) return "just now";
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  const mins = secs / 60;
  if (mins < 60) return `${Math.floor(mins)}m ago`;
  const hours = mins / 60;
  if (hours < 24) return `${Math.floor(hours)}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
