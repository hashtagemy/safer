import { cn } from "@/lib/utils";
import { HTMLAttributes } from "react";

type Variant = "default" | "ice" | "success" | "warning" | "critical" | "muted" | "outline";

export function Badge({
  className,
  variant = "default",
  ...props
}: HTMLAttributes<HTMLDivElement> & { variant?: Variant }) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium font-mono",
        variantStyles(variant),
        className
      )}
      {...props}
    />
  );
}

function variantStyles(v: Variant): string {
  switch (v) {
    case "ice":
      return "bg-safer-ice/15 text-safer-ice border-safer-ice/30";
    case "success":
      return "bg-safer-success/15 text-safer-success border-safer-success/30";
    case "warning":
      return "bg-safer-warning/15 text-safer-warning border-safer-warning/30";
    case "critical":
      return "bg-safer-critical/15 text-safer-critical border-safer-critical/30";
    case "muted":
      return "bg-muted text-muted-foreground border-border";
    case "outline":
      return "bg-transparent border-border text-foreground";
    default:
      return "bg-primary/15 text-primary border-primary/30";
  }
}

export function RiskBadge({ risk }: { risk: string }) {
  const map: Record<string, Variant> = {
    LOW: "success",
    MEDIUM: "ice",
    HIGH: "warning",
    CRITICAL: "critical",
  };
  return <Badge variant={map[risk] ?? "muted"}>{risk}</Badge>;
}

export function HookBadge({ hook }: { hook: string }) {
  return (
    <Badge variant="outline" className="font-normal">
      {hook}
    </Badge>
  );
}
