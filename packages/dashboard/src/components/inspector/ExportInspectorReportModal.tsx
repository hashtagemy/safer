import { useEffect, useState } from "react";
import { Download, X } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import {
  DEFAULT_SELECTIONS,
  ExportFormat,
  ExportSection,
  ExportSelections,
  SECTION_LABELS,
  buildExport,
  triggerBrowserDownload,
} from "@/lib/inspector-export";
import type { InspectorReport } from "@/lib/inspector-types";

export interface ExportInspectorReportModalProps {
  report: InspectorReport;
  open: boolean;
  onClose: () => void;
}

const SECTION_ORDER: ExportSection[] = [
  "header",
  "ast",
  "findings",
  "patterns",
  "suggestions",
  "files",
];

const FORMAT_OPTIONS: Array<{ value: ExportFormat; label: string; hint: string }> = [
  { value: "markdown", label: "Markdown (.md)", hint: "Readable, paste-ready" },
  { value: "html", label: "HTML (.html)", hint: "Print → Save as PDF" },
  { value: "json", label: "JSON (.json)", hint: "Full structured data" },
];

export function ExportInspectorReportModal({
  report,
  open,
  onClose,
}: ExportInspectorReportModalProps) {
  const [selections, setSelections] = useState<ExportSelections>(
    DEFAULT_SELECTIONS
  );

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  const toggleSection = (key: ExportSection) => {
    setSelections((s) => ({
      ...s,
      sections: { ...s.sections, [key]: !s.sections[key] },
    }));
  };
  const pickFormat = (format: ExportFormat) => {
    setSelections((s) => ({ ...s, format }));
  };

  const nothingSelected = Object.values(selections.sections).every((v) => !v);

  const handleDownload = () => {
    const payload = buildExport(report, selections);
    triggerBrowserDownload(payload);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm animate-fadein"
      role="dialog"
      aria-modal
      onClick={(e) => {
        // Click on backdrop closes; click inside modal does not.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[520px] max-w-[95vw] rounded-lg border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border p-4">
          <div>
            <h2 className="text-base font-semibold">Export Inspector report</h2>
            <p className="text-[11px] text-muted-foreground font-mono mt-1">
              Pick what to include and a format. Output is built in the browser
              and downloaded directly.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            title="Close (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <section>
            <h3 className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
              Sections
            </h3>
            <div className="space-y-1">
              {SECTION_ORDER.map((key) => (
                <label
                  key={key}
                  className="flex items-center gap-2 text-xs font-mono cursor-pointer select-none rounded px-2 py-1 hover:bg-muted/40"
                >
                  <input
                    type="checkbox"
                    checked={selections.sections[key]}
                    onChange={() => toggleSection(key)}
                    className="h-3.5 w-3.5 accent-safer-ice"
                  />
                  <span>{SECTION_LABELS[key]}</span>
                </label>
              ))}
            </div>
          </section>

          <section>
            <h3 className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
              Format
            </h3>
            <div className="space-y-1">
              {FORMAT_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className={cn(
                    "flex items-center gap-2 text-xs font-mono cursor-pointer select-none rounded border px-2 py-1.5",
                    selections.format === opt.value
                      ? "border-safer-ice/50 bg-safer-ice/5"
                      : "border-border bg-card/40 hover:bg-muted/40"
                  )}
                >
                  <input
                    type="radio"
                    name="export-format"
                    checked={selections.format === opt.value}
                    onChange={() => pickFormat(opt.value)}
                    className="h-3.5 w-3.5 accent-safer-ice"
                  />
                  <span className="flex-1">{opt.label}</span>
                  <span className="text-[10px] text-muted-foreground">
                    {opt.hint}
                  </span>
                </label>
              ))}
            </div>
          </section>

          {nothingSelected && (
            <Badge variant="warning">Pick at least one section</Badge>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border p-4">
          <button
            onClick={onClose}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-mono hover:bg-muted/40 transition"
          >
            Cancel
          </button>
          <button
            onClick={handleDownload}
            disabled={nothingSelected}
            className={cn(
              "rounded-md border px-3 py-1.5 text-xs font-mono inline-flex items-center gap-1.5 transition",
              "border-safer-ice/50 bg-safer-ice/10 text-foreground hover:bg-safer-ice/20",
              "disabled:opacity-40 disabled:cursor-not-allowed"
            )}
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </button>
        </div>
      </div>
    </div>
  );
}
