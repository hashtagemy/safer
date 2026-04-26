import { useCallback, useEffect, useState } from "react";
import { Trash2, Sparkles, CheckCircle2, AlertTriangle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { BACKEND_URL, fetchJSON } from "@/lib/api";

type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
type GuardMode = "monitor" | "intervene" | "enforce";
type Variant = "ice" | "success" | "warning" | "critical" | "muted" | "outline";

interface PolicyTestCase {
  description: string;
  event: Record<string, unknown>;
  expected_block: boolean;
  expected_flag: string | null;
}

interface CompiledPolicy {
  name: string;
  nl_text: string;
  rule_json: Record<string, unknown>;
  code_snippet: string | null;
  flag_category: string;
  flag: string;
  severity: Severity;
  guard_mode: GuardMode;
  test_cases: PolicyTestCase[];
}

interface ActivePolicy extends CompiledPolicy {
  policy_id: string;
  agent_id: string | null;
  active: boolean;
  created_at: string;
}

interface PolicyListResponse {
  policies: ActivePolicy[];
}

interface AgentSummary {
  agent_id: string;
  name: string;
  framework: string | null;
}

const severityVariant: Record<Severity, Variant> = {
  LOW: "success",
  MEDIUM: "ice",
  HIGH: "warning",
  CRITICAL: "critical",
};

const guardVariant: Record<GuardMode, Variant> = {
  monitor: "muted",
  intervene: "warning",
  enforce: "critical",
};

export default function Policies() {
  const [activePolicies, setActivePolicies] = useState<ActivePolicy[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const [nlText, setNlText] = useState("");
  const [compiling, setCompiling] = useState(false);
  const [compileError, setCompileError] = useState<string | null>(null);
  const [compiled, setCompiled] = useState<CompiledPolicy | null>(null);

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[]>([]);

  const [activating, setActivating] = useState(false);
  const [activateError, setActivateError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const loadPolicies = useCallback(async () => {
    try {
      const r = await fetchJSON<PolicyListResponse>("/v1/policies?active_only=true");
      setActivePolicies(r.policies);
      setListError(null);
    } catch (e) {
      setActivePolicies([]);
      setListError((e as Error).message);
    }
  }, []);

  const loadAgents = useCallback(async () => {
    try {
      const r = await fetchJSON<AgentSummary[]>("/v1/agents");
      setAgents(r);
    } catch {
      setAgents([]);
    }
  }, []);

  useEffect(() => {
    loadPolicies();
    loadAgents();
  }, [loadPolicies, loadAgents]);

  const toggleAgent = (agentId: string) => {
    setSelectedAgentIds((prev) =>
      prev.includes(agentId)
        ? prev.filter((a) => a !== agentId)
        : [...prev, agentId]
    );
  };

  const compile = async () => {
    if (!nlText.trim()) return;
    setCompiling(true);
    setCompileError(null);
    setCompiled(null);
    try {
      const r = await fetch(`${BACKEND_URL}/v1/policies/compile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nl_text: nlText.trim(),
          // Hand the compiler the first selected agent's tool surface
          // so it binds to real argument names instead of guessing.
          agent_id: selectedAgentIds[0] ?? null,
        }),
      });
      if (!r.ok) {
        const body = await r.text();
        throw new Error(`${r.status}: ${body.slice(0, 300)}`);
      }
      const data = (await r.json()) as CompiledPolicy;
      setCompiled(data);
    } catch (e) {
      setCompileError((e as Error).message);
    } finally {
      setCompiling(false);
    }
  };

  const activate = async () => {
    if (!compiled) return;
    setActivating(true);
    setActivateError(null);
    try {
      const r = await fetch(`${BACKEND_URL}/v1/policies/activate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          compiled,
          agent_ids: selectedAgentIds,
        }),
      });
      if (!r.ok) {
        const body = await r.text();
        throw new Error(`${r.status}: ${body.slice(0, 300)}`);
      }
      const targetCount = selectedAgentIds.length || 1;
      const targetLabel =
        selectedAgentIds.length === 0
          ? "globally"
          : `on ${targetCount} agent${targetCount === 1 ? "" : "s"}`;
      setFlash(`Activated "${compiled.name}" ${targetLabel}`);
      setCompiled(null);
      setNlText("");
      setSelectedAgentIds([]);
      await loadPolicies();
      window.setTimeout(() => setFlash(null), 3000);
    } catch (e) {
      setActivateError((e as Error).message);
    } finally {
      setActivating(false);
    }
  };

  const deactivate = async (policyId: string) => {
    try {
      const r = await fetch(`${BACKEND_URL}/v1/policies/${policyId}`, {
        method: "DELETE",
      });
      if (!r.ok && r.status !== 204) {
        throw new Error(`${r.status}: ${r.statusText}`);
      }
      await loadPolicies();
    } catch (e) {
      setListError((e as Error).message);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Policy Studio</h1>
          <p className="text-sm text-muted-foreground">
            Write a policy in natural language. Opus 4.7 compiles it to a
            deterministic Gateway rule with test cases you can review before
            activating.
          </p>
        </div>
        {flash && (
          <div className="flex items-center gap-2 text-sm text-safer-success border border-safer-success/30 bg-safer-success/10 rounded-md px-3 py-1.5 animate-fadein">
            <CheckCircle2 className="h-4 w-4" />
            {flash}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ActivePoliciesColumn
          policies={activePolicies}
          error={listError}
          onDeactivate={deactivate}
        />
        <ComposeColumn
          nlText={nlText}
          setNlText={setNlText}
          compiling={compiling}
          compileError={compileError}
          onCompile={compile}
          agents={agents}
          selectedAgentIds={selectedAgentIds}
          onToggleAgent={toggleAgent}
        />
        <PreviewColumn
          compiled={compiled}
          activating={activating}
          activateError={activateError}
          onActivate={activate}
        />
      </div>
    </div>
  );
}

function ActivePoliciesColumn({
  policies,
  error,
  onDeactivate,
}: {
  policies: ActivePolicy[] | null;
  error: string | null;
  onDeactivate: (policyId: string) => void;
}) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-base">Active policies</CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          Built-in rules always on (PII Guard, Tool Allowlist, Loop Detection,
          Prompt Injection Guard). User-compiled policies show below.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && (
          <p className="text-xs text-safer-critical font-mono">
            backend: {error}
          </p>
        )}
        {policies === null ? (
          <p className="text-xs text-muted-foreground font-mono">loading…</p>
        ) : policies.length === 0 ? (
          <p className="text-xs text-muted-foreground font-mono">
            No user-compiled policies yet.
          </p>
        ) : (
          policies.map((p) => (
            <div
              key={p.policy_id}
              className="rounded-md border border-border bg-card/50 p-3 space-y-2"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="text-sm font-medium font-mono break-all">
                  {p.name}
                </div>
                <button
                  onClick={() => onDeactivate(p.policy_id)}
                  className="text-muted-foreground hover:text-safer-critical transition"
                  aria-label={`Deactivate ${p.name}`}
                  title="Deactivate"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
              <p className="text-xs text-muted-foreground line-clamp-2">
                {p.nl_text}
              </p>
              <div className="flex flex-wrap items-center gap-1">
                <Badge variant={severityVariant[p.severity]}>{p.severity}</Badge>
                <Badge variant={guardVariant[p.guard_mode]}>{p.guard_mode}</Badge>
                <Badge variant="outline">{p.rule_json.kind as string}</Badge>
                {p.agent_id && (
                  <Badge variant="outline">agent={p.agent_id}</Badge>
                )}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function ComposeColumn({
  nlText,
  setNlText,
  compiling,
  compileError,
  onCompile,
  agents,
  selectedAgentIds,
  onToggleAgent,
}: {
  nlText: string;
  setNlText: (v: string) => void;
  compiling: boolean;
  compileError: string | null;
  onCompile: () => void;
  agents: AgentSummary[];
  selectedAgentIds: string[];
  onToggleAgent: (agentId: string) => void;
}) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-base">Compose</CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          Example: "Never let this agent email customer addresses to an
          external domain."
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <textarea
          value={nlText}
          onChange={(e) => setNlText(e.target.value)}
          placeholder="Describe a rule the agent must follow..."
          rows={6}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-safer-ice"
          disabled={compiling}
        />

        <div className="space-y-2">
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-mono">
            Apply to which agents?
          </div>
          {agents.length === 0 ? (
            <p className="text-xs text-muted-foreground font-mono">
              No agents registered yet — leave empty for a global policy.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-1.5 max-h-48 overflow-auto pr-1">
              {agents.map((a) => {
                const checked = selectedAgentIds.includes(a.agent_id);
                return (
                  <label
                    key={a.agent_id}
                    className={`flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs font-mono cursor-pointer transition ${
                      checked
                        ? "border-safer-ice bg-safer-ice/10"
                        : "border-border hover:bg-muted/40"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onToggleAgent(a.agent_id)}
                      className="accent-safer-ice"
                      disabled={compiling}
                    />
                    <span className="truncate flex-1">{a.name}</span>
                    {a.framework && (
                      <Badge variant="outline">{a.framework}</Badge>
                    )}
                  </label>
                );
              })}
            </div>
          )}
          <p className="text-[11px] text-muted-foreground font-mono">
            {selectedAgentIds.length === 0
              ? "Empty selection → global policy (applies to every agent)."
              : `Compiler will use "${
                  agents.find((a) => a.agent_id === selectedAgentIds[0])
                    ?.name ?? selectedAgentIds[0]
                }" for context; Activate creates ${selectedAgentIds.length} policy row${
                  selectedAgentIds.length === 1 ? "" : "s"
                }.`}
          </p>
        </div>

        <button
          onClick={onCompile}
          disabled={compiling || !nlText.trim()}
          className="w-full inline-flex items-center justify-center gap-2 rounded-md bg-safer-ice px-3 py-2 text-sm font-medium text-safer-bg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition"
        >
          <Sparkles className="h-4 w-4" />
          {compiling ? "Compiling with Opus 4.7…" : "Compile"}
        </button>
        {compileError && (
          <div className="flex items-start gap-2 text-xs text-safer-critical font-mono">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="break-all">{compileError}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PreviewColumn({
  compiled,
  activating,
  activateError,
  onActivate,
}: {
  compiled: CompiledPolicy | null;
  activating: boolean;
  activateError: string | null;
  onActivate: () => void;
}) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-base">Preview</CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          Review the compiled rule + test cases before activating.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {!compiled ? (
          <p className="text-xs text-muted-foreground font-mono">
            No compiled policy yet. Write something in the middle panel and
            press Compile.
          </p>
        ) : (
          <>
            <div className="space-y-2">
              <div className="text-sm font-medium font-mono break-all">
                {compiled.name}
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <Badge variant={severityVariant[compiled.severity]}>
                  {compiled.severity}
                </Badge>
                <Badge variant={guardVariant[compiled.guard_mode]}>
                  {compiled.guard_mode}
                </Badge>
                <Badge variant="outline">
                  {compiled.rule_json.kind as string}
                </Badge>
                <Badge variant="outline">{compiled.flag}</Badge>
              </div>
            </div>

            <div>
              <div className="text-xs text-muted-foreground mb-1">rule_json</div>
              <pre className="rounded-md border border-border bg-background/40 p-3 text-xs overflow-x-auto font-mono">
                {JSON.stringify(compiled.rule_json, null, 2)}
              </pre>
            </div>

            {compiled.test_cases.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs text-muted-foreground">test cases</div>
                {compiled.test_cases.map((tc, i) => (
                  <div
                    key={i}
                    className="rounded-md border border-border bg-card/50 p-2 space-y-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-mono">{tc.description}</span>
                      <Badge variant={tc.expected_block ? "critical" : "success"}>
                        {tc.expected_block ? "BLOCK" : "ALLOW"}
                      </Badge>
                    </div>
                    <pre className="text-[11px] text-muted-foreground overflow-x-auto font-mono">
                      {JSON.stringify(tc.event, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            )}

            <button
              onClick={onActivate}
              disabled={activating}
              className="w-full inline-flex items-center justify-center gap-2 rounded-md bg-safer-success px-3 py-2 text-sm font-medium text-safer-bg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition"
            >
              <CheckCircle2 className="h-4 w-4" />
              {activating ? "Activating…" : "Activate policy"}
            </button>
            {activateError && (
              <div className="flex items-start gap-2 text-xs text-safer-critical font-mono">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <span className="break-all">{activateError}</span>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
