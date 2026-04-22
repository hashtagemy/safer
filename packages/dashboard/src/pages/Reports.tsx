import { useEffect, useMemo, useRef, useState } from "react";
import {
  FileDown,
  FileText,
  FileJson,
  AlertTriangle,
  Printer,
  Sparkles,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { BACKEND_URL } from "@/lib/api";

type Standard = "gdpr" | "soc2" | "owasp_llm";
type Format = "html" | "pdf" | "json";

const STANDARDS: Array<{ value: Standard; label: string; hint: string }> = [
  { value: "gdpr", label: "GDPR", hint: "Data protection posture, PII findings, access log" },
  { value: "soc2", label: "SOC 2", hint: "Trust Services Criteria, control failures, audit trail" },
  { value: "owasp_llm", label: "OWASP LLM", hint: "Top 10 category map with samples & mitigations" },
];

function isoDay(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function defaultRange(): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 7);
  return { start: isoDay(start), end: isoDay(end) };
}

export default function Reports() {
  const initial = useMemo(defaultRange, []);
  const [startDate, setStartDate] = useState(initial.start);
  const [endDate, setEndDate] = useState(initial.end);
  const [standard, setStandard] = useState<Standard>("gdpr");
  const [format, setFormat] = useState<Format>("html");
  const [agentId, setAgentId] = useState("");

  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [htmlPreview, setHtmlPreview] = useState<string | null>(null);
  const [jsonPreview, setJsonPreview] = useState<unknown | null>(null);
  const [downloadedName, setDownloadedName] = useState<string | null>(null);

  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    // Reset preview whenever the inputs change so stale results don't linger.
    setHtmlPreview(null);
    setJsonPreview(null);
    setDownloadedName(null);
    setError(null);
  }, [startDate, endDate, standard, format, agentId]);

  const build = async () => {
    setBuilding(true);
    setError(null);
    setHtmlPreview(null);
    setJsonPreview(null);
    setDownloadedName(null);

    const body = {
      standard,
      start_date: startDate,
      end_date: endDate,
      format,
      agent_id: agentId.trim() || null,
    };

    try {
      const resp = await fetch(`${BACKEND_URL}/v1/reports/build`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const detail = await resp.text();
        throw new Error(`${resp.status}: ${detail.slice(0, 300)}`);
      }
      const filename = extractFilename(resp) ?? `safer-${standard}.${format}`;

      if (format === "html") {
        const text = await resp.text();
        setHtmlPreview(text);
      } else if (format === "json") {
        const data = await resp.json();
        setJsonPreview(data);
        downloadBlob(
          new Blob([JSON.stringify(data, null, 2)], {
            type: "application/json",
          }),
          filename
        );
        setDownloadedName(filename);
      } else {
        // pdf
        const blob = await resp.blob();
        downloadBlob(blob, filename);
        setDownloadedName(filename);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBuilding(false);
    }
  };

  const downloadHtml = () => {
    if (!htmlPreview) return;
    const name =
      `safer-${standard}-${startDate}-to-${endDate}.html`.replace(/:/g, "-");
    downloadBlob(new Blob([htmlPreview], { type: "text/html" }), name);
  };

  const printHtml = () => {
    iframeRef.current?.contentWindow?.focus();
    iframeRef.current?.contentWindow?.print();
  };

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Compliance Pack</h1>
        <p className="text-sm text-muted-foreground">
          Generate a GDPR / SOC 2 / OWASP LLM report over any date range.
          Output is rendered from the live database — re-running with the
          same inputs is reproducible.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Build a report</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-sm">
            <label className="block">
              <span className="text-xs text-muted-foreground font-mono">start</span>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
              />
            </label>
            <label className="block">
              <span className="text-xs text-muted-foreground font-mono">end</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
              />
            </label>
            <label className="block">
              <span className="text-xs text-muted-foreground font-mono">standard</span>
              <select
                value={standard}
                onChange={(e) => setStandard(e.target.value as Standard)}
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
              >
                {STANDARDS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-muted-foreground font-mono">format</span>
              <select
                value={format}
                onChange={(e) => setFormat(e.target.value as Format)}
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
              >
                <option value="html">HTML (preview + download)</option>
                <option value="pdf">PDF (download)</option>
                <option value="json">JSON (preview + download)</option>
              </select>
            </label>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
            <label className="block md:col-span-2">
              <span className="text-xs text-muted-foreground font-mono">
                agent_id filter (optional)
              </span>
              <input
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                placeholder="leave empty to include every agent"
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
              />
            </label>
            <div className="flex items-end">
              <button
                onClick={build}
                disabled={building}
                className="w-full inline-flex items-center justify-center gap-2 rounded-md bg-safer-ice px-3 py-2 text-sm font-medium text-safer-bg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition"
              >
                <Sparkles className="h-4 w-4" />
                {building ? "Building…" : "Build report"}
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap text-xs font-mono">
            {STANDARDS.filter((s) => s.value === standard).map((s) => (
              <Badge key={s.value} variant="outline">
                {s.label}
              </Badge>
            ))}
            <Badge variant="outline">{format.toUpperCase()}</Badge>
            <span className="text-muted-foreground">
              {STANDARDS.find((s) => s.value === standard)?.hint}
            </span>
          </div>

          {error && (
            <div className="flex items-start gap-2 text-xs text-safer-critical font-mono">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="break-all">{error}</span>
            </div>
          )}

          {downloadedName && (
            <div className="flex items-center gap-2 text-xs text-safer-success font-mono">
              <FileDown className="h-4 w-4" />
              Downloaded {downloadedName}
            </div>
          )}
        </CardContent>
      </Card>

      {htmlPreview && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <FileText className="h-4 w-4 text-safer-ice" />
                HTML preview
              </CardTitle>
              <div className="flex items-center gap-2">
                <button
                  onClick={printHtml}
                  className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-mono hover:bg-muted/40 transition"
                >
                  <Printer className="h-3.5 w-3.5" />
                  Print / Save as PDF
                </button>
                <button
                  onClick={downloadHtml}
                  className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-mono hover:bg-muted/40 transition"
                >
                  <FileDown className="h-3.5 w-3.5" />
                  Download HTML
                </button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <iframe
              ref={iframeRef}
              title="Compliance preview"
              srcDoc={htmlPreview}
              className="w-full h-[700px] rounded-md border border-border bg-white"
            />
          </CardContent>
        </Card>
      )}

      {jsonPreview !== null && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <FileJson className="h-4 w-4 text-safer-ice" />
              JSON preview
            </CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs font-mono bg-muted/40 rounded-md p-3 overflow-auto max-h-[600px]">
              {JSON.stringify(jsonPreview, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function extractFilename(resp: Response): string | null {
  const disp = resp.headers.get("content-disposition");
  if (!disp) return null;
  const m = disp.match(/filename=([^;]+)/);
  return m ? m[1].trim().replace(/^"|"$/g, "") : null;
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
