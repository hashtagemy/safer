import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

export default function Agents() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
        <p className="text-sm text-muted-foreground">
          Instrumented agents and Inspector onboarding scans.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Add Agent</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground font-mono">
            Phase 8 — Inspector (AST + deterministic patterns + 3-persona Opus
            review + auto policy suggestions) enables here.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
