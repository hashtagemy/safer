import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

export default function Quality() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Quality</h1>
        <p className="text-sm text-muted-foreground">
          Session-level quality scores and trends.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Session scores</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground font-mono">
            Phase 11 Quality Reviewer (Opus) aggregates Judge verdicts to
            task-completion + hallucination + efficiency + goal-drift signals.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
