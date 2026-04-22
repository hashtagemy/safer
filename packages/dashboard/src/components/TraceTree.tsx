import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge, HookBadge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import type { SessionEvent } from "@/lib/sessionTypes";

/**
 * Groups a session's events into nested calls:
 *   session
 *     ├── LLM call #1 (before/after_llm_call pair)
 *     ├── Tool call #1 (before/after_tool_use pair)
 *     └── ...
 *
 * Only event sequence is used to pair — the SDK always emits
 * before/after in order, so a simple stack-based walk is enough.
 */
interface Node {
  kind: "llm" | "tool" | "decision" | "final" | "error";
  before: SessionEvent;
  after?: SessionEvent;
  children: Node[];
}

function buildTree(events: SessionEvent[]): Node[] {
  const roots: Node[] = [];
  const stack: Node[] = [];

  const push = (node: Node) => {
    if (stack.length === 0) {
      roots.push(node);
    } else {
      stack[stack.length - 1].children.push(node);
    }
    stack.push(node);
  };
  const pop = (expectHook: string): Node | null => {
    for (let i = stack.length - 1; i >= 0; i--) {
      const candidate = stack[i];
      const matches =
        (expectHook === "after_llm_call" && candidate.kind === "llm") ||
        (expectHook === "after_tool_use" && candidate.kind === "tool");
      if (matches) {
        stack.splice(i, stack.length - i);
        return candidate;
      }
    }
    return null;
  };

  for (const ev of events) {
    switch (ev.hook) {
      case "before_llm_call":
        push({ kind: "llm", before: ev, children: [] });
        break;
      case "after_llm_call": {
        const n = pop("after_llm_call");
        if (n) n.after = ev;
        break;
      }
      case "before_tool_use":
        push({ kind: "tool", before: ev, children: [] });
        break;
      case "after_tool_use": {
        const n = pop("after_tool_use");
        if (n) n.after = ev;
        break;
      }
      case "on_agent_decision":
        if (stack.length === 0) {
          roots.push({ kind: "decision", before: ev, children: [] });
        } else {
          stack[stack.length - 1].children.push({
            kind: "decision",
            before: ev,
            children: [],
          });
        }
        break;
      case "on_final_output":
        roots.push({ kind: "final", before: ev, children: [] });
        break;
      case "on_error":
        roots.push({ kind: "error", before: ev, children: [] });
        break;
      default:
        break;
    }
  }
  return roots;
}

function nodeLabel(n: Node): string {
  const p = n.before.payload as Record<string, unknown>;
  if (n.kind === "llm") return `LLM · ${(p.model as string) || "claude"}`;
  if (n.kind === "tool") return `Tool · ${(p.tool_name as string) || "?"}`;
  if (n.kind === "decision") return "Decision";
  if (n.kind === "final") return "Final output";
  if (n.kind === "error") return "Error";
  return n.kind;
}

function duration(n: Node): string | null {
  if (!n.after) return null;
  const ms =
    new Date(n.after.timestamp).getTime() - new Date(n.before.timestamp).getTime();
  if (ms <= 0) return null;
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function NodeRow({
  node,
  depth,
  onSelect,
}: {
  node: Node;
  depth: number;
  onSelect: (ev: SessionEvent) => void;
}) {
  const [open, setOpen] = useState(depth === 0);
  const hasChildren = node.children.length > 0;
  return (
    <div className="space-y-1" style={{ marginLeft: depth === 0 ? 0 : 12 }}>
      <div className="flex items-center gap-2 text-xs font-mono rounded-md border border-border bg-card/40 px-2 py-1.5">
        {hasChildren ? (
          <button
            onClick={() => setOpen(!open)}
            className="text-muted-foreground hover:text-foreground"
          >
            {open ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="w-3.5" />
        )}
        <Badge
          variant={
            node.kind === "error"
              ? "critical"
              : node.kind === "final"
              ? "ice"
              : "outline"
          }
        >
          {node.kind}
        </Badge>
        <button
          onClick={() => onSelect(node.before)}
          className="flex-1 text-left truncate hover:underline decoration-dotted"
        >
          {nodeLabel(node)}
        </button>
        {node.after && (
          <HookBadge hook={node.after.hook} />
        )}
        {duration(node) && (
          <span className="text-muted-foreground">{duration(node)}</span>
        )}
      </div>
      {open && hasChildren && (
        <div className="border-l border-border/60 pl-3 space-y-1">
          {node.children.map((c) => (
            <NodeRow
              key={c.before.event_id}
              node={c}
              depth={depth + 1}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function TraceTree({
  events,
  onSelect,
}: {
  events: SessionEvent[];
  onSelect: (ev: SessionEvent) => void;
}) {
  const tree = buildTree(events);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Trace tree</CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          LLM and tool calls paired by sequence. Click a node to open the
          persona drawer on that event.
        </p>
      </CardHeader>
      <CardContent>
        {tree.length === 0 ? (
          <p className="text-xs text-muted-foreground font-mono">
            No matched LLM/tool calls yet.
          </p>
        ) : (
          <div className="space-y-1">
            {tree.map((n) => (
              <NodeRow
                key={n.before.event_id}
                node={n}
                depth={0}
                onSelect={onSelect}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
