import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ShieldAlert,
  Play,
  X,
  Download,
  ChevronDown,
  ChevronRight,
  Target,
  Sword,
  Microscope,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { BACKEND_URL, fetchJSON } from "@/lib/api";
import { useSaferRealtime } from "@/lib/ws";
import { cn } from "@/lib/utils";

// --- backend types (mirror pydantic models) ---

type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
type Variant = "ice" | "success" | "warning" | "critical" | "muted" | "outline";

interface AttackSpec {
  attack_id: string;
  category: string;
  title: string;
  prompt: string;
  expected_behavior: string;
  seed_template: string | null;
}

interface Attempt {
  attempt_id: string;
  run_id: string;
  attack_id: string;
  result: "success" | "partial" | "blocked";
  evidence: string[];
  agent_response: string | null;
  latency_ms: number;
  notes: string | null;
  timestamp: string;
}

interface RedTeamRun {
  run_id: string;
  agent_id: string;
  mode: "managed" | "subagent";
  phase: "planning" | "attacking" | "analyzing" | "done" | "failed";
  started_at: string;
  finished_at: string | null;
  attack_specs: AttackSpec[];
  attempts: Attempt[];
  findings_count: number;
  safety_score: number;
  owasp_map: Record<string, number>;
  error: string | null;
}

interface Finding {
  finding_id: string;
  severity: Severity;
  category: string;
  flag: string;
  title: string;
  description: string;
  evidence: string[];
  reproduction_steps: string[];
  recommended_mitigation: string | null;
  owasp_id: string | null;
}

const OWASP_ROWS: Array<{ id: string; label: string }> = [
  { id: "owasp_llm01_prompt_injection", label: "LLM01 — Prompt Injection" },
  { id: "owasp_llm02_insecure_output_handling", label: "LLM02 — Insecure Output Handling" },
  { id: "owasp_llm03_training_data_poisoning", label: "LLM03 — Training Data Poisoning" },
  { id: "owasp_llm04_model_denial_of_service", label: "LLM04 — Model DoS" },
  { id: "owasp_llm05_supply_chain", label: "LLM05 — Supply Chain" },
  { id: "owasp_llm06_sensitive_info_disclosure", label: "LLM06 — Sensitive Info Disclosure" },
  { id: "owasp_llm07_insecure_plugin_design", label: "LLM07 — Insecure Plugin Design" },
  { id: "owasp_llm08_excessive_agency", label: "LLM08 — Excessive Agency" },
  { id: "owasp_llm09_overreliance", label: "LLM09 — Overreliance" },
  { id: "owasp_llm10_model_theft", label: "LLM10 — Model Theft" },
];

const PHASE_LABELS: Record<
  RedTeamRun["phase"],
  { icon: typeof Target; label: string }
> = {
  planning: { icon: Target, label: "Strategist" },
  attacking: { icon: Sword, label: "Attacker" },
  analyzing: { icon: Microscope, label: "Analyst" },
  done: { icon: CheckCircle2, label: "Done" },
  failed: { icon: AlertTriangle, label: "Failed" },
};

const severityVariant: Record<Severity, Variant> = {
  LOW: "success",
  MEDIUM: "ice",
  HIGH: "warning",
  CRITICAL: "critical",
};

function resultTone(result: Attempt["result"]): Variant {
  return result === "success" ? "critical" : result === "partial" ? "warning" : "success";
}

function scoreTone(score: number): string {
  if (score >= 90) return "text-safer-success";
  if (score >= 70) return "text-safer-ice";
  if (score >= 40) return "text-safer-warning";
  return "text-safer-critical";
}

export default function RedTeam() {
  const { redteamByRunId } = useSaferRealtime(500);

  const [runs, setRuns] = useState<RedTeamRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<RedTeamRun | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string>(
    () => localStorage.getItem("safer.redteam.agentId") ?? ""
  );
  const [loadingList, setLoadingList] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [showModal, setShowModal] = useState(false);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const loadRuns = useCallback(
    async (agentId: string) => {
      if (!agentId) {
        setRuns([]);
        return;
      }
      setLoadingList(true);
      setListError(null);
      try {
        const list = await fetchJSON<RedTeamRun[]>(
          `/v1/agents/${encodeURIComponent(agentId)}/redteam/runs?limit=20`
        );
        setRuns(list);
      } catch (e) {
        setListError((e as Error).message);
        setRuns([]);
      } finally {
        setLoadingList(false);
      }
    },
    []
  );

  useEffect(() => {
    loadRuns(selectedAgentId);
  }, [loadRuns, selectedAgentId]);

  useEffect(() => {
    if (!selectedRun) return;
    const live = redteamByRunId[selectedRun.run_id];
    if (!live) return;
    setSelectedRun((prev) =>
      prev
        ? {
            ...prev,
            phase: live.phase,
            findings_count: live.findings_count ?? prev.findings_count,
            safety_score: live.safety_score ?? prev.safety_score,
            owasp_map: live.owasp_map ?? prev.owasp_map,
            error: live.error ?? prev.error,
          }
        : prev
    );
  }, [redteamByRunId, selectedRun]);

  const handleRun = async (input: {
    agent_id: string;
    target_system_prompt: string;
    target_tools: string[];
    target_name: string;
    num_attacks: number;
    mode: "managed" | "subagent";
  }) => {
    setRunning(true);
    setRunError(null);
    localStorage.setItem("safer.redteam.agentId", input.agent_id);
    setSelectedAgentId(input.agent_id);
    try {
      const r = await fetch(
        `${BACKEND_URL}/v1/agents/${encodeURIComponent(input.agent_id)}/redteam/run`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target_system_prompt: input.target_system_prompt,
            target_tools: input.target_tools,
            target_name: input.target_name,
            num_attacks: input.num_attacks,
            mode: input.mode,
          }),
        }
      );
      if (!r.ok) {
        throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
      }
      const run = (await r.json()) as RedTeamRun;
      setSelectedRun(run);
      setShowModal(false);
      await loadRuns(input.agent_id);
    } catch (e) {
      setRunError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Red-Team Squad</h1>
          <p className="text-sm text-muted-foreground">
            Manual adversarial evaluation via three Claude agents —
            Strategist → Attacker → Analyst. Always user-triggered.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <AgentPicker
            value={selectedAgentId}
            onChange={(v) => {
              setSelectedAgentId(v);
              localStorage.setItem("safer.redteam.agentId", v);
            }}
          />
          <button
            onClick={() => setShowModal(true)}
            className="inline-flex items-center gap-2 rounded-md bg-safer-critical px-3 py-2 text-sm font-medium text-safer-bg hover:opacity-90 transition"
          >
            <ShieldAlert className="h-4 w-4" />
            Run Red-Team
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <PastRunsColumn
          runs={runs}
          selectedRun={selectedRun}
          onSelect={setSelectedRun}
          loading={loadingList}
          error={listError}
        />
        <div className="lg:col-span-2 space-y-4">
          <PhaseStrip run={selectedRun} live={selectedRun ? redteamByRunId[selectedRun.run_id] : undefined} />
          {selectedRun ? (
            <>
              <RunHeader run={selectedRun} />
              <OwaspMap run={selectedRun} />
              <FindingsList run={selectedRun} />
              <AttemptsList run={selectedRun} />
            </>
          ) : (
            <Card>
              <CardContent className="p-6 text-sm text-muted-foreground font-mono">
                Pick a run on the left or press <b>Run Red-Team</b> to start a
                new one. The Strategist will generate a tailored attack list,
                the Attacker will simulate the target's responses, and the
                Analyst will cluster the attempts into findings.
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {showModal && (
        <RunModal
          initialAgentId={selectedAgentId}
          running={running}
          error={runError}
          onClose={() => setShowModal(false)}
          onSubmit={handleRun}
        />
      )}
    </div>
  );
}

function AgentPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-muted-foreground font-mono">
      agent_id
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="agent_id"
        className="rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice w-48"
      />
    </label>
  );
}

function PastRunsColumn({
  runs,
  selectedRun,
  onSelect,
  loading,
  error,
}: {
  runs: RedTeamRun[];
  selectedRun: RedTeamRun | null;
  onSelect: (r: RedTeamRun) => void;
  loading: boolean;
  error: string | null;
}) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-base">Past runs</CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          20 most recent runs for the selected agent.
        </p>
      </CardHeader>
      <CardContent className="space-y-2">
        {loading && (
          <p className="text-xs text-muted-foreground font-mono">loading…</p>
        )}
        {error && (
          <p className="text-xs text-safer-critical font-mono">
            backend: {error}
          </p>
        )}
        {!loading && runs.length === 0 && !error && (
          <p className="text-xs text-muted-foreground font-mono">
            No runs yet for this agent.
          </p>
        )}
        {runs.map((r) => (
          <button
            key={r.run_id}
            onClick={() => onSelect(r)}
            className={cn(
              "w-full text-left rounded-md border p-3 space-y-2 transition",
              selectedRun?.run_id === r.run_id
                ? "border-safer-ice/50 bg-safer-ice/5"
                : "border-border bg-card/50 hover:bg-muted/40"
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium font-mono truncate">
                {r.run_id}
              </span>
              <Badge variant={r.phase === "done" ? "success" : r.phase === "failed" ? "critical" : "ice"}>
                {r.phase}
              </Badge>
            </div>
            <div className="flex items-center gap-2 text-xs font-mono">
              <span className={cn("font-semibold", scoreTone(r.safety_score))}>
                {r.safety_score}
              </span>
              <span className="text-muted-foreground">safety</span>
              <span className="ml-auto text-muted-foreground">
                {r.findings_count} findings
              </span>
            </div>
            <div className="flex items-center gap-1 text-[10px] text-muted-foreground font-mono">
              <Badge variant="outline">{r.mode}</Badge>
              <span>{new Date(r.started_at).toLocaleString()}</span>
            </div>
          </button>
        ))}
      </CardContent>
    </Card>
  );
}

function PhaseStrip({
  run,
  live,
}: {
  run: RedTeamRun | null;
  live: ReturnType<typeof useSaferRealtime>["redteamByRunId"][string];
}) {
  const currentPhase = live?.phase ?? run?.phase ?? "planning";
  const phases: RedTeamRun["phase"][] = ["planning", "attacking", "analyzing", "done"];
  const currentIdx = phases.indexOf(currentPhase);
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center gap-4 flex-wrap">
          {phases.map((p, i) => {
            const { icon: Icon, label } = PHASE_LABELS[p];
            const active = i <= currentIdx;
            return (
              <div
                key={p}
                className={cn(
                  "flex items-center gap-2 text-xs font-mono",
                  active ? "text-foreground" : "text-muted-foreground opacity-40"
                )}
              >
                <Icon
                  className={cn(
                    "h-4 w-4",
                    active && i === currentIdx && "animate-pulse",
                    active ? "text-safer-ice" : "text-muted-foreground"
                  )}
                />
                <span>{label}</span>
                {i < phases.length - 1 && (
                  <span className={cn("mx-1", active ? "text-safer-ice" : "text-muted-foreground")}>
                    →
                  </span>
                )}
              </div>
            );
          })}
          {currentPhase === "failed" && (
            <div className="text-xs text-safer-critical font-mono flex items-center gap-1">
              <AlertTriangle className="h-3.5 w-3.5" />
              failed
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function RunHeader({ run }: { run: RedTeamRun }) {
  return (
    <Card>
      <CardContent className="p-4 flex flex-wrap items-center gap-4">
        <div>
          <div className={cn("text-4xl font-semibold leading-none", scoreTone(run.safety_score))}>
            {run.safety_score}
          </div>
          <div className="text-[11px] text-muted-foreground font-mono mt-1">
            safety score
          </div>
        </div>
        <div className="flex-1 grid grid-cols-2 gap-x-6 gap-y-1 text-xs font-mono">
          <dt className="text-muted-foreground">run_id</dt>
          <dd className="break-all">{run.run_id}</dd>
          <dt className="text-muted-foreground">mode</dt>
          <dd>{run.mode}</dd>
          <dt className="text-muted-foreground">attacks</dt>
          <dd>
            {run.attempts.length}/{run.attack_specs.length}
          </dd>
          <dt className="text-muted-foreground">findings</dt>
          <dd>{run.findings_count}</dd>
        </div>
        <ExportButton run={run} />
      </CardContent>
    </Card>
  );
}

function OwaspMap({ run }: { run: RedTeamRun }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">OWASP LLM Top 10</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {OWASP_ROWS.map((row) => {
          const count = run.owasp_map[row.id] ?? 0;
          const tone =
            count === 0
              ? "border-border bg-card/30 text-muted-foreground"
              : count >= 3
              ? "border-safer-critical/40 bg-safer-critical/10 text-safer-critical"
              : "border-safer-warning/40 bg-safer-warning/10 text-safer-warning";
          return (
            <div
              key={row.id}
              className={cn(
                "rounded-md border px-3 py-2 flex items-center justify-between gap-2 text-xs font-mono",
                tone
              )}
            >
              <span className="truncate">{row.label}</span>
              <span className="font-semibold">{count}</span>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function AttemptsList({ run }: { run: RedTeamRun }) {
  const specsById = useMemo(() => {
    const m = new Map<string, AttackSpec>();
    run.attack_specs.forEach((s) => m.set(s.attack_id, s));
    return m;
  }, [run]);

  if (run.attempts.length === 0) {
    return null;
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Attempts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {run.attempts.map((a) => (
          <AttemptRow key={a.attempt_id} attempt={a} spec={specsById.get(a.attack_id)} />
        ))}
      </CardContent>
    </Card>
  );
}

function AttemptRow({
  attempt,
  spec,
}: {
  attempt: Attempt;
  spec: AttackSpec | undefined;
}) {
  const [open, setOpen] = useState(attempt.result !== "blocked");
  return (
    <div className="rounded-md border border-border bg-card/40 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full text-left p-3 flex items-center gap-2 hover:bg-muted/30 transition"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        )}
        <Badge variant={resultTone(attempt.result)}>
          {attempt.result.toUpperCase()}
        </Badge>
        {spec && <Badge variant="outline">{spec.category}</Badge>}
        <span className="text-xs font-mono truncate">
          {spec?.title ?? attempt.attack_id}
        </span>
        <span className="ml-auto text-[11px] text-muted-foreground font-mono">
          {attempt.latency_ms} ms
        </span>
      </button>
      {open && (
        <div className="px-3 pb-3 border-t border-border/60 text-xs space-y-2 animate-fadein">
          {spec && (
            <div>
              <div className="text-[11px] uppercase text-muted-foreground mt-2 mb-1">
                attacker prompt
              </div>
              <p className="font-mono text-xs bg-muted/40 rounded-md p-2 whitespace-pre-wrap">
                {spec.prompt}
              </p>
            </div>
          )}
          {attempt.agent_response && (
            <div>
              <div className="text-[11px] uppercase text-muted-foreground mb-1">
                simulated target response
              </div>
              <p className="font-mono text-xs bg-muted/40 rounded-md p-2 whitespace-pre-wrap">
                {attempt.agent_response}
              </p>
            </div>
          )}
          {attempt.evidence.length > 0 && (
            <div>
              <div className="text-[11px] uppercase text-muted-foreground mb-1">
                evidence
              </div>
              <ul className="list-disc list-inside font-mono">
                {attempt.evidence.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          )}
          {attempt.notes && (
            <div className="text-[11px] text-muted-foreground font-mono">
              {attempt.notes}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FindingsList({ run }: { run: RedTeamRun }) {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (run.phase !== "done" || run.findings_count === 0) {
        setFindings([]);
        return;
      }
      try {
        // Findings table query isn't exposed yet as a dedicated endpoint; the
        // aggregator already joins them into SessionReport, and orchestrator
        // writes them into `findings`. For now surface the counts + link to
        // attempts; a dedicated GET /v1/redteam/runs/{id}/findings could
        // follow — not shipped in this phase.
        if (!cancelled) setFindings([]);
      } catch (e) {
        if (!cancelled) setErr((e as Error).message);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [run]);

  if (run.findings_count === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Findings</CardTitle>
        <p className="text-xs text-muted-foreground font-mono">
          {run.findings_count} clustered finding
          {run.findings_count === 1 ? "" : "s"}. See the Attempts list below for
          evidence and responses.
        </p>
      </CardHeader>
      {err && (
        <CardContent>
          <p className="text-xs text-safer-critical font-mono">{err}</p>
        </CardContent>
      )}
      {findings && findings.length > 0 && (
        <CardContent className="space-y-2">
          {findings.map((f) => (
            <div
              key={f.finding_id}
              className="rounded-md border border-border bg-card/40 p-3 space-y-1"
            >
              <div className="flex items-center gap-2">
                <Badge variant={severityVariant[f.severity]}>{f.severity}</Badge>
                <span className="text-xs font-mono">{f.title}</span>
                {f.owasp_id && <Badge variant="outline">{f.owasp_id}</Badge>}
              </div>
              <p className="text-xs text-muted-foreground">{f.description}</p>
              {f.recommended_mitigation && (
                <p className="text-[11px] font-mono">
                  <span className="text-muted-foreground">mitigation: </span>
                  {f.recommended_mitigation}
                </p>
              )}
            </div>
          ))}
        </CardContent>
      )}
    </Card>
  );
}

function ExportButton({ run }: { run: RedTeamRun }) {
  const download = () => {
    const blob = new Blob([JSON.stringify(run, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `redteam-${run.run_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <button
      onClick={download}
      className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-mono hover:bg-muted/40 transition"
    >
      <Download className="h-3.5 w-3.5" />
      Export JSON
    </button>
  );
}

function RunModal({
  initialAgentId,
  running,
  error,
  onClose,
  onSubmit,
}: {
  initialAgentId: string;
  running: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (input: {
    agent_id: string;
    target_system_prompt: string;
    target_tools: string[];
    target_name: string;
    num_attacks: number;
    mode: "managed" | "subagent";
  }) => void;
}) {
  const [agentId, setAgentId] = useState(initialAgentId || "agent_demo");
  const [targetName, setTargetName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [tools, setTools] = useState("get_order, send_email");
  const [numAttacks, setNumAttacks] = useState(10);
  const [mode, setMode] = useState<"managed" | "subagent">("subagent");

  const canSubmit = agentId.trim() && systemPrompt.trim() && !running;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fadein">
      <Card className="w-[560px] max-w-[95vw] max-h-[90vh] overflow-auto">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <ShieldAlert className="h-4 w-4 text-safer-critical" />
              Run Red-Team
            </CardTitle>
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground transition"
              disabled={running}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <LabeledInput label="agent_id" value={agentId} onChange={setAgentId} />
          <LabeledInput
            label="target_name"
            value={targetName}
            onChange={setTargetName}
            placeholder="(optional)"
          />
          <label className="block">
            <span className="text-xs text-muted-foreground font-mono">
              target_system_prompt
            </span>
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
              placeholder="You are a customer-support agent for Acme Corp..."
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
            />
          </label>
          <LabeledInput
            label="target_tools (comma-separated)"
            value={tools}
            onChange={setTools}
            placeholder="get_order, send_email"
          />
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs text-muted-foreground font-mono">
                num_attacks
              </span>
              <input
                type="number"
                min={1}
                max={30}
                value={numAttacks}
                onChange={(e) => setNumAttacks(parseInt(e.target.value || "1", 10))}
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
              />
            </label>
            <label className="block">
              <span className="text-xs text-muted-foreground font-mono">
                mode
              </span>
              <select
                value={mode}
                onChange={(e) =>
                  setMode(e.target.value as "managed" | "subagent")
                }
                className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
              >
                <option value="subagent">subagent (MVP)</option>
                <option value="managed">managed (auto-fallback)</option>
              </select>
            </label>
          </div>

          {error && (
            <div className="flex items-start gap-2 text-xs text-safer-critical font-mono">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="break-all">{error}</span>
            </div>
          )}

          <button
            onClick={() =>
              onSubmit({
                agent_id: agentId.trim(),
                target_name: targetName.trim(),
                target_system_prompt: systemPrompt.trim(),
                target_tools: tools
                  .split(",")
                  .map((t) => t.trim())
                  .filter(Boolean),
                num_attacks: Math.max(1, Math.min(30, numAttacks)),
                mode,
              })
            }
            disabled={!canSubmit}
            className="w-full inline-flex items-center justify-center gap-2 rounded-md bg-safer-critical px-3 py-2 text-sm font-medium text-safer-bg hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            <Play className="h-4 w-4" />
            {running ? "Running… (Opus calls in flight)" : "Start run"}
          </button>
        </CardContent>
      </Card>
    </div>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs text-muted-foreground font-mono">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-safer-ice"
      />
    </label>
  );
}
