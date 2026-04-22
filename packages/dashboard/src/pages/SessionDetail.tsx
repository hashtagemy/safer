import { useParams } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

export default function SessionDetail() {
  const { id } = useParams();
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight font-mono">
          {id}
        </h1>
        <p className="text-sm text-muted-foreground">Session Report + Thought-Chain</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Session Report</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground font-mono">
            Phase 14 renders the 7-category health card, Thought-Chain
            narrative streaming, timeline, and trace tree here.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
