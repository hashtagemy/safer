import { useEffect, useState } from "react";
import { ShieldAlert, X, Eye } from "lucide-react";
import { BlockMsg } from "@/lib/ws";
import { Badge } from "@/components/ui/Badge";

const AUTO_DISMISS_MS = 10_000;

export interface BlockMomentToastProps {
  block: BlockMsg | null;
  onExplain: (block: BlockMsg) => void;
  onDismiss: () => void;
}

/**
 * Bottom-right toast that surfaces the most recent gateway/judge block
 * signal. Auto-dismisses after 10s. The "Explain" button asks the host
 * page to open the matching event in the PersonaDrawer.
 */
export function BlockMomentToast({
  block,
  onExplain,
  onDismiss,
}: BlockMomentToastProps) {
  // Track the block we actually render, so an auto-dismiss timer reset
  // only fires when a NEW block arrives.
  const [shown, setShown] = useState<BlockMsg | null>(null);

  useEffect(() => {
    if (!block) return;
    setShown(block);
    const t = window.setTimeout(() => {
      setShown(null);
      onDismiss();
    }, AUTO_DISMISS_MS);
    return () => clearTimeout(t);
  }, [block, onDismiss]);

  if (!shown) return null;

  const risk = shown.risk ?? "CRITICAL";
  const source = shown.source ?? "gateway";
  const reason = shown.reason ?? "Policy violation";

  return (
    <div
      role="alert"
      className="fixed bottom-6 right-6 z-50 w-[360px] animate-fadein"
    >
      <div className="rounded-lg border border-safer-critical/50 bg-safer-critical/10 backdrop-blur-sm shadow-lg animate-pulse-critical">
        <div className="p-3 flex items-start gap-3">
          <div className="mt-0.5 text-safer-critical">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm font-semibold text-safer-critical">
                Blocked
              </span>
              <Badge variant="outline">{source}</Badge>
              <Badge variant="critical">{risk}</Badge>
            </div>
            <p className="text-xs text-muted-foreground font-mono break-all line-clamp-2">
              {reason}
            </p>
            {shown.hits && shown.hits.length > 0 && (
              <p className="text-[11px] text-muted-foreground font-mono mt-1">
                policies: {shown.hits.map((h) => h.flag).join(", ")}
              </p>
            )}
            <div className="mt-2 flex items-center gap-2">
              <button
                onClick={() => onExplain(shown)}
                className="inline-flex items-center gap-1 text-xs font-medium text-safer-ice hover:opacity-80 transition"
              >
                <Eye className="h-3.5 w-3.5" /> Explain
              </button>
            </div>
          </div>
          <button
            onClick={() => {
              setShown(null);
              onDismiss();
            }}
            className="text-muted-foreground hover:text-foreground transition"
            aria-label="Dismiss"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
