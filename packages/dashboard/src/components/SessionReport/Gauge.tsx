import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

interface GaugeProps {
  value: number; // 0..100
  size?: number;
  label?: string;
}

function tone(value: number) {
  if (value >= 90) return { color: "#10B981", text: "text-safer-success" };
  if (value >= 70) return { color: "#7DD3FC", text: "text-safer-ice" };
  if (value >= 40) return { color: "#F59E0B", text: "text-safer-warning" };
  return { color: "#EF4444", text: "text-safer-critical" };
}

/**
 * Animated score dial. Renders an SVG ring that fills from 0 to `value`
 * over ~600ms so the card feels alive when a report loads.
 */
export function Gauge({ value, size = 140, label = "Overall health" }: GaugeProps) {
  const [animated, setAnimated] = useState(0);
  const { color, text } = tone(value);

  useEffect(() => {
    let raf = 0;
    const start = performance.now();
    const duration = 700;
    const from = animated;
    const to = Math.max(0, Math.min(100, value));
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setAnimated(from + (to - from) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const stroke = 10;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference * (1 - animated / 100);

  return (
    <div className="flex flex-col items-center gap-1" style={{ width: size }}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke="rgba(148,163,184,0.15)"
            strokeWidth={stroke}
            fill="none"
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke={color}
            strokeWidth={stroke}
            fill="none"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
            style={{ transition: "stroke 200ms ease" }}
          />
        </svg>
        <div
          className={cn(
            "absolute inset-0 flex flex-col items-center justify-center",
            text
          )}
        >
          <div className="text-3xl font-semibold tabular-nums">
            {Math.round(animated)}
          </div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {label}
          </div>
        </div>
      </div>
    </div>
  );
}
