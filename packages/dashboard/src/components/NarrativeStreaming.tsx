import { useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";

interface NarrativeStreamingProps {
  narrative: string | null;
  /** Characters per tick; bumps perceived speed. */
  charsPerTick?: number;
  /** ms between ticks. */
  intervalMs?: number;
}

/**
 * Client-side typewriter effect. Server already returned the full
 * narrative inside the SessionReport; replaying it char-by-char is
 * lighter than a real SSE stream and looks identical for the user.
 */
export function NarrativeStreaming({
  narrative,
  charsPerTick = 6,
  intervalMs = 18,
}: NarrativeStreamingProps) {
  const [shown, setShown] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    setShown("");
    setDone(false);
    if (!narrative) return;
    let i = 0;
    const id = window.setInterval(() => {
      i = Math.min(i + charsPerTick, narrative.length);
      setShown(narrative.slice(0, i));
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
      if (i >= narrative.length) {
        window.clearInterval(id);
        setDone(true);
      }
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [narrative, charsPerTick, intervalMs]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Thought-Chain narrative</CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          Reconstructed by Opus 4.7 from the session's event trace.
        </p>
      </CardHeader>
      <CardContent>
        {!narrative ? (
          <p className="text-sm text-muted-foreground font-mono">
            No narrative on this report yet. Click <b>Reconstruct</b> above to
            have Opus 4.7 generate one from the event trace.
          </p>
        ) : (
          <div
            ref={scrollRef}
            className="text-sm leading-relaxed max-h-[320px] overflow-auto whitespace-pre-wrap"
          >
            {shown}
            <span
              className={cn(
                "inline-block ml-0.5 w-1.5 h-4 align-middle bg-safer-ice rounded-sm",
                done ? "opacity-0" : "animate-pulse"
              )}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
