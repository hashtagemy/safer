import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

export default function Reports() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Compliance Pack</h1>
        <p className="text-sm text-muted-foreground">
          Export GDPR / SOC 2 / OWASP LLM Top 10 audit packages as PDF / HTML
          / JSON across any time range.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Build a report</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground font-mono">
            Phase 13 wires up the time-range picker and the WeasyPrint PDF
            renderer.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
