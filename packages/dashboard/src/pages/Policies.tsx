import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

export default function Policies() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Policy Studio</h1>
        <p className="text-sm text-muted-foreground">
          Write policies in natural language. Claude compiles them to rules
          enforced at the Gateway and fed to the Policy Warden persona.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>NL → Rule compiler</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground font-mono">
            Phase 9 enables natural-language compile (Opus 4.7) with preview +
            activate.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
