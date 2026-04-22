import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Shield, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { HookBadge, RiskBadge, Badge } from "@/components/ui/Badge";
import {
  SaferEvent,
  VerdictMsg,
  PreStepScoreMsg,
  PersonaVerdictMsg,
} from "@/lib/ws";

const ALL_PERSONAS: Array<{ key: string; label: string }> = [
  { key: "security_auditor", label: "Security Auditor" },
  { key: "compliance_officer", label: "Compliance Officer" },
  { key: "trust_guardian", label: "Trust Guardian" },
  { key: "scope_enforcer", label: "Scope Enforcer" },
  { key: "ethics_reviewer", label: "Ethics Reviewer" },
  { key: "policy_warden", label: "Policy Warden" },
];

function scoreTone(score: number): string {
  if (score >= 90) return "bg-safer-success";
  if (score >= 70) return "bg-safer-ice";
  if (score >= 40) return "bg-safer-warning";
  return "bg-safer-critical";
}

export interface PersonaDrawerProps {
  event: SaferEvent | null;
  verdict: VerdictMsg | undefined;
  prestep: PreStepScoreMsg | undefined;
  onClose: () => void;
}

export function PersonaDrawer({
  event,
  verdict,
  prestep,
  onClose,
}: PersonaDrawerProps) {
  useEffect(() => {
    if (!event) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [event, onClose]);

  if (!event) return null;

  const gateway = event.gateway;
  const activeSet = new Set(verdict?.active_personas ?? []);

  return (
    <aside className="w-[460px] shrink-0 border-l border-border bg-card/40 flex flex-col animate-fadein">
      <div className="p-4 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2 flex-wrap">
          <HookBadge hook={event.hook} />
          <RiskBadge risk={event.risk_hint} />
          {verdict?.overall.block && (
            <Badge variant="critical">BLOCK</Badge>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-muted-foreground text-xs hover:text-foreground inline-flex items-center gap-1"
          title="Close (Esc)"
        >
          <X className="h-3.5 w-3.5" /> esc
        </button>
      </div>

      <div className="p-4 space-y-5 overflow-auto text-sm">
        <MetaSection event={event} />

        {gateway && <GatewaySection gateway={gateway} />}

        {prestep && <PrestepSection prestep={prestep} />}

        {verdict ? (
          <VerdictSection verdict={verdict} />
        ) : (
          <section>
            <SectionTitle>persona verdicts</SectionTitle>
            <p className="text-xs text-muted-foreground font-mono">
              No Judge verdict for this hook yet. Verdicts arrive for
              before_tool_use, on_agent_decision, and on_final_output.
            </p>
          </section>
        )}

        <section>
          <SectionTitle>persona set</SectionTitle>
          <div className="grid grid-cols-2 gap-2">
            {ALL_PERSONAS.map((p) => {
              const isActive = activeSet.has(p.key);
              return (
                <div
                  key={p.key}
                  className={cn(
                    "rounded-md border px-2 py-1.5 flex items-center gap-2 text-xs font-mono",
                    isActive
                      ? "border-safer-ice/40 bg-safer-ice/5 text-foreground"
                      : "border-border bg-muted/20 text-muted-foreground"
                  )}
                >
                  <Shield
                    className={cn(
                      "h-3.5 w-3.5",
                      isActive ? "text-safer-ice" : "text-muted-foreground"
                    )}
                  />
                  <span>{p.label}</span>
                  {!isActive && (
                    <span className="ml-auto text-[10px] opacity-60">
                      idle
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        <PayloadSection payload={event.payload} />
      </div>
    </aside>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
      {children}
    </div>
  );
}

function MetaSection({ event }: { event: SaferEvent }) {
  return (
    <section>
      <SectionTitle>meta</SectionTitle>
      <dl className="text-xs font-mono space-y-0.5">
        <Row k="event_id" v={event.event_id} />
        <Row k="session_id" v={event.session_id} />
        <Row k="agent_id" v={event.agent_id} />
        <Row k="sequence" v={String(event.sequence)} />
        <Row k="timestamp" v={event.timestamp} />
      </dl>
    </section>
  );
}

function GatewaySection({
  gateway,
}: {
  gateway: NonNullable<SaferEvent["gateway"]>;
}) {
  const tone =
    gateway.decision === "block"
      ? "critical"
      : gateway.decision === "warn"
      ? "warning"
      : "success";
  return (
    <section>
      <SectionTitle>gateway</SectionTitle>
      <div className="flex items-center gap-2 text-xs font-mono mb-2">
        <Badge variant={tone as "critical" | "warning" | "success"}>
          {gateway.decision.toUpperCase()}
        </Badge>
        <RiskBadge risk={gateway.risk} />
      </div>
      {gateway.reason && (
        <p className="text-xs text-muted-foreground font-mono mb-2">
          {gateway.reason}
        </p>
      )}
      {gateway.hits.length > 0 && (
        <div className="space-y-1">
          {gateway.hits.map((h, i) => (
            <div
              key={i}
              className="rounded-md border border-border bg-card/50 p-2 text-xs font-mono"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="break-all">{h.policy_name}</span>
                <Badge variant="outline">{h.flag}</Badge>
              </div>
              {h.evidence.length > 0 && (
                <ul className="mt-1 text-[11px] text-muted-foreground space-y-0.5">
                  {h.evidence.map((e, j) => (
                    <li key={j} className="break-all">
                      · {e}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function PrestepSection({ prestep }: { prestep: PreStepScoreMsg }) {
  return (
    <section>
      <SectionTitle>per-step haiku</SectionTitle>
      <div className="flex items-center gap-2 text-xs font-mono">
        <span className="text-muted-foreground">relevance</span>
        <span>{prestep.relevance_score}</span>
        {prestep.should_escalate && (
          <Badge variant="warning">escalate</Badge>
        )}
      </div>
      {prestep.reason && (
        <p className="text-xs text-muted-foreground font-mono mt-1">
          {prestep.reason}
        </p>
      )}
    </section>
  );
}

function VerdictSection({ verdict }: { verdict: VerdictMsg }) {
  return (
    <section>
      <SectionTitle>judge verdict</SectionTitle>
      <div className="flex flex-wrap items-center gap-2 text-xs font-mono mb-3">
        <RiskBadge risk={verdict.overall.risk} />
        <span className="text-muted-foreground">
          conf {verdict.overall.confidence.toFixed(2)}
        </span>
        {verdict.overall.block && <Badge variant="critical">BLOCK</Badge>}
        <span className="text-muted-foreground ml-auto">
          {verdict.latency_ms} ms
        </span>
      </div>

      <div className="space-y-2">
        {verdict.active_personas.map((p) => {
          const pv = verdict.personas[p];
          if (!pv) return null;
          return <PersonaCard key={p} persona={p} verdict={pv} />;
        })}
      </div>
    </section>
  );
}

function PersonaCard({
  persona,
  verdict,
}: {
  persona: string;
  verdict: PersonaVerdictMsg;
}) {
  const [open, setOpen] = useState(
    verdict.flags.length > 0 || verdict.score < 70
  );
  const label =
    ALL_PERSONAS.find((p) => p.key === persona)?.label ?? persona;
  return (
    <div className="rounded-md border border-border bg-card/50 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full p-2 text-left flex items-center gap-2 hover:bg-muted/30 transition"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        )}
        <span className="text-xs font-medium font-mono">{label}</span>
        <div className="ml-auto flex items-center gap-2">
          <ScoreBar score={verdict.score} />
          <span className="text-xs font-mono w-8 text-right">
            {verdict.score}
          </span>
        </div>
      </button>
      {open && (
        <div className="px-3 pb-3 text-xs space-y-2 border-t border-border/60 animate-fadein">
          <div className="flex items-center gap-2 pt-2 text-muted-foreground font-mono">
            <span>conf</span>
            <span className="text-foreground">
              {verdict.confidence.toFixed(2)}
            </span>
          </div>
          {verdict.flags.length > 0 && (
            <div>
              <div className="text-[11px] uppercase text-muted-foreground mb-1">
                flags
              </div>
              <div className="flex flex-wrap gap-1">
                {verdict.flags.map((f) => (
                  <Badge key={f} variant="outline">
                    {f}
                  </Badge>
                ))}
              </div>
            </div>
          )}
          {verdict.evidence.length > 0 && (
            <div>
              <div className="text-[11px] uppercase text-muted-foreground mb-1">
                evidence
              </div>
              <ul className="text-[11px] font-mono space-y-0.5">
                {verdict.evidence.map((e, i) => (
                  <li key={i} className="break-all">
                    “{e}”
                  </li>
                ))}
              </ul>
            </div>
          )}
          {verdict.reasoning && (
            <div>
              <div className="text-[11px] uppercase text-muted-foreground mb-1">
                reasoning
              </div>
              <p className="text-xs font-mono leading-relaxed">
                {verdict.reasoning}
              </p>
            </div>
          )}
          {verdict.recommended_mitigation && (
            <div>
              <div className="text-[11px] uppercase text-muted-foreground mb-1">
                recommended mitigation
              </div>
              <p className="text-xs font-mono leading-relaxed">
                {verdict.recommended_mitigation}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ScoreBar({ score }: { score: number }) {
  const clamped = Math.max(0, Math.min(100, score));
  return (
    <div className="w-20 h-1.5 rounded-full bg-muted overflow-hidden">
      <div
        className={cn("h-full rounded-full transition-all", scoreTone(clamped))}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

function PayloadSection({ payload }: { payload: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  return (
    <section>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-muted-foreground hover:text-foreground transition"
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        raw payload
      </button>
      {open && (
        <pre className="mt-2 text-xs font-mono bg-muted/40 rounded-md p-3 overflow-auto max-h-80 animate-fadein">
          {JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </section>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-start gap-2">
      <dt className="text-muted-foreground w-24 shrink-0">{k}</dt>
      <dd className="break-all">{v}</dd>
    </div>
  );
}
