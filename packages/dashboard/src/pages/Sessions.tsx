import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

export default function Sessions() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
        <p className="text-sm text-muted-foreground">
          All agent sessions — click one to see the Session Report card.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Recent sessions</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground font-mono">
            Phase 14 populates this table with overall health scores and
            category badges per session.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
