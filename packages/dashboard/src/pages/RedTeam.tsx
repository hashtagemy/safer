import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

export default function RedTeam() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Red-Team Squad</h1>
        <p className="text-sm text-muted-foreground">
          Manual stress testing via 3 Claude Managed Agents (Strategist →
          Attacker → Analyst). Always user-triggered, never continuous.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Run Red-Team</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground font-mono">
            Phase 12 enables the orchestrator with OWASP LLM Top 10 mapping,
            phase progress, and findings. Plan B sub-agent fallback runs in
            parallel if the Managed Agents API is unavailable.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
