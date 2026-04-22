import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ShieldAlert,
  Eye,
  Ban,
  Trash2,
  Database,
  Sparkles,
  Swords,
  Cpu,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { BACKEND_URL, fetchJSON } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Stats {
  agents: number;
  sessions: number;
  events: number;
  active_sessions: number;
}

interface CostSummary {
  total_usd: number;
  today_usd: number;
  total_calls: number;
  by_component: Record<string, number>;
}

type GuardMode = "monitor" | "intervene" | "enforce";
type JudgeMode = "auto" | "on" | "off";
type RedteamMode = "managed" | "subagent";

interface ConfigSnapshot {
  guard_mode: GuardMode;
  judge_enabled: JudgeMode;
  judge_max_tokens: number;
  redteam_default_mode: RedteamMode;
  redteam_default_num_attacks: number;
  retention_days: number;
  valid_guard_modes: GuardMode[];
  valid_judge_modes: JudgeMode[];
  valid_redteam_modes: RedteamMode[];
}

interface SystemInfo {
  safer_version: string;
  python_version: string;
  platform: string;
  uptime_seconds: number;
  db_path: string;
  db_size_bytes: number;
  judge_model: string;
  haiku_model: string;
  policy_compiler_model: string;
  redteam_model: string;
  total_opus_calls: number;
  total_haiku_calls: number;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cache_read_tokens: number;
  cache_read_ratio: number;
}

const GUARD_MODES: Array<{ value: GuardMode; label: string; blurb: string; icon: typeof Eye }> = [
  { value: "monitor", label: "Monitor", blurb: "Log only; never blocks.", icon: Eye },
  { value: "intervene", label: "Intervene", blurb: "Block CRITICAL + high-impact flags.", icon: ShieldAlert },
  { value: "enforce", label: "Enforce", blurb: "Block every HIGH-or-worse hit.", icon: Ban },
];

const JUDGE_MODES: Array<{ value: JudgeMode; label: string; blurb: string }> = [
  { value: "auto", label: "Auto", blurb: "On iff ANTHROPIC_API_KEY is set." },
  { value: "on", label: "On", blurb: "Always run the Judge." },
  { value: "off", label: "Off", blurb: "Skip the Judge everywhere." },
];

const REDTEAM_MODES: Array<{ value: RedteamMode; label: string; blurb: string }> = [
  { value: "subagent", label: "Sub-agent", blurb: "Plain Opus calls; works everywhere." },
  { value: "managed", label: "Managed", blurb: "Claude Managed Agents; auto-fallback to sub-agent." },
];

function humanBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function humanUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
  }
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  return `${d}d ${h}h`;
}

export default function Settings() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [cost, setCost] = useState<CostSummary | null>(null);
  const [config, setConfig] = useState<ConfigSnapshot | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    try {
      const [s, c, cfg, sys] = await Promise.all([
        fetchJSON<Stats>("/v1/stats"),
        fetchJSON<CostSummary>("/v1/stats/cost"),
        fetchJSON<ConfigSnapshot>("/v1/config"),
        fetchJSON<SystemInfo>("/v1/system"),
      ]);
      setStats(s);
      setCost(c);
      setConfig(cfg);
      setSystem(sys);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    loadAll();
    const t = window.setInterval(loadAll, 10_000);
    return () => clearInterval(t);
  }, [loadAll]);

  const showFlash = (msg: string) => {
    setFlash(msg);
    window.setTimeout(() => setFlash(null), 2500);
  };

  const patchConfig = async (patch: Partial<Record<keyof ConfigSnapshot, unknown>>, label: string) => {
    setError(null);
    try {
      const r = await fetch(`${BACKEND_URL}/v1/config`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      setConfig((await r.json()) as ConfigSnapshot);
      showFlash(label);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const purgeOldEvents = async () => {
    if (!config) return;
    if (!window.confirm(
      `Delete every event older than ${config.retention_days} days? Sessions stay; verdicts/findings/claude_calls in that window are removed.`
    )) {
      return;
    }
    setError(null);
    try {
      const r = await fetch(
        `${BACKEND_URL}/v1/admin/events?older_than_days=${config.retention_days}`,
        { method: "DELETE" }
      );
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      const body = await r.json();
      showFlash(
        `Purged ${body.deleted_events} events / ${body.deleted_findings} findings`
      );
      loadAll();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const vacuumDb = async () => {
    if (!window.confirm("Run VACUUM on the SQLite database? This briefly locks writes.")) return;
    setError(null);
    try {
      const r = await fetch(`${BACKEND_URL}/v1/admin/vacuum`, { method: "POST" });
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      const body = await r.json();
      showFlash(`VACUUM reclaimed ${humanBytes(body.bytes_reclaimed)}`);
      loadAll();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="text-sm text-muted-foreground">
            Runtime configuration, data management, and system info.
          </p>
        </div>
        {flash && (
          <div className="flex items-center gap-1 text-xs text-safer-success border border-safer-success/30 bg-safer-success/10 rounded-md px-3 py-1.5 animate-fadein">
            <CheckCircle2 className="h-4 w-4" />
            {flash}
          </div>
        )}
      </div>

      {error && (
        <Card>
          <CardContent className="p-4 flex items-start gap-2 text-xs text-safer-critical font-mono">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="break-all">{error}</span>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* ---------- Guard mode ---------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-safer-ice" />
            Gateway guard mode
          </CardTitle>
          <p className="text-xs text-muted-foreground font-mono">
            Controls how the Gateway reacts to a policy hit. Switch applies
            to the next event; the SDK learns on its next call.
          </p>
        </CardHeader>
        <CardContent>
          {config === null ? (
            <p className="text-xs text-muted-foreground font-mono">loading…</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              {GUARD_MODES.map((m) => {
                const active = config.guard_mode === m.value;
                const Icon = m.icon;
                return (
                  <button
                    key={m.value}
                    onClick={() => patchConfig({ guard_mode: m.value }, `Guard mode → ${m.value}`)}
                    disabled={active}
                    className={cn(
                      "text-left rounded-md border p-3 transition",
                      active
                        ? "border-safer-ice/60 bg-safer-ice/10 cursor-default"
                        : "border-border hover:bg-muted/40 cursor-pointer"
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Icon
                        className={cn("h-4 w-4", active ? "text-safer-ice" : "text-muted-foreground")}
                      />
                      <span className="text-sm font-medium font-mono">{m.label}</span>
                      {active && <Badge variant="ice" className="ml-auto">active</Badge>}
                    </div>
                    <p className="mt-1 text-[11px] text-muted-foreground">{m.blurb}</p>
                  </button>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ---------- Judge ---------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-safer-ice" />
            Multi-Persona Judge
          </CardTitle>
          <p className="text-xs text-muted-foreground font-mono">
            Opus 4.7 runs only on the three decision hooks, only with the
            personas the event needs. Every call uses prompt caching.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {config && (
            <>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                {JUDGE_MODES.map((m) => {
                  const active = config.judge_enabled === m.value;
                  return (
                    <button
                      key={m.value}
                      onClick={() => patchConfig({ judge_enabled: m.value }, `Judge → ${m.value}`)}
                      disabled={active}
                      className={cn(
                        "text-left rounded-md border p-3 transition",
                        active
                          ? "border-safer-ice/60 bg-safer-ice/10 cursor-default"
                          : "border-border hover:bg-muted/40 cursor-pointer"
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium font-mono">{m.label}</span>
                        {active && <Badge variant="ice" className="ml-auto">active</Badge>}
                      </div>
                      <p className="mt-1 text-[11px] text-muted-foreground">{m.blurb}</p>
                    </button>
                  );
                })}
              </div>
              <NumberField
                label="max output tokens per call"
                value={config.judge_max_tokens}
                min={256}
                max={8000}
                step={128}
                onCommit={(n) => patchConfig({ judge_max_tokens: n }, `Judge max_tokens → ${n}`)}
              />
            </>
          )}
        </CardContent>
      </Card>

      {/* ---------- Red-Team defaults ---------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Swords className="h-4 w-4 text-safer-ice" />
            Red-Team defaults
          </CardTitle>
          <p className="text-xs text-muted-foreground font-mono">
            Pre-fill the /redteam modal. The run page can still override
            either field per run.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {config && (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {REDTEAM_MODES.map((m) => {
                  const active = config.redteam_default_mode === m.value;
                  return (
                    <button
                      key={m.value}
                      onClick={() =>
                        patchConfig(
                          { redteam_default_mode: m.value },
                          `Red-Team mode → ${m.value}`
                        )
                      }
                      disabled={active}
                      className={cn(
                        "text-left rounded-md border p-3 transition",
                        active
                          ? "border-safer-ice/60 bg-safer-ice/10 cursor-default"
                          : "border-border hover:bg-muted/40 cursor-pointer"
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium font-mono">{m.label}</span>
                        {active && <Badge variant="ice" className="ml-auto">active</Badge>}
                      </div>
                      <p className="mt-1 text-[11px] text-muted-foreground">{m.blurb}</p>
                    </button>
                  );
                })}
              </div>
              <NumberField
                label="default attack count (1-30)"
                value={config.redteam_default_num_attacks}
                min={1}
                max={30}
                step={1}
                onCommit={(n) =>
                  patchConfig(
                    { redteam_default_num_attacks: n },
                    `Red-Team default attacks → ${n}`
                  )
                }
              />
            </>
          )}
        </CardContent>
      </Card>

      {/* ---------- Data management ---------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Database className="h-4 w-4 text-safer-ice" />
            Data management
          </CardTitle>
          <p className="text-xs text-muted-foreground font-mono">
            SQLite is append-only. Purge old events to shrink the DB; VACUUM
            reclaims the freed pages.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {config && (
            <NumberField
              label="retention window (days)"
              value={config.retention_days}
              min={1}
              max={3650}
              step={1}
              onCommit={(n) =>
                patchConfig({ retention_days: n }, `Retention → ${n} days`)
              }
            />
          )}
          <div className="flex flex-wrap gap-2">
            <button
              onClick={purgeOldEvents}
              className="inline-flex items-center gap-2 rounded-md border border-safer-warning/40 bg-safer-warning/10 text-safer-warning px-3 py-2 text-xs font-mono hover:opacity-90 transition"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Purge events older than retention
            </button>
            <button
              onClick={vacuumDb}
              className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-xs font-mono hover:bg-muted/40 transition"
            >
              <Database className="h-3.5 w-3.5" />
              VACUUM database
            </button>
          </div>
        </CardContent>
      </Card>

      {/* ---------- Backend status ---------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Backend status</CardTitle>
        </CardHeader>
        <CardContent>
          {stats ? (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm font-mono">
              <dt className="text-muted-foreground">agents</dt>
              <dd>{stats.agents}</dd>
              <dt className="text-muted-foreground">sessions</dt>
              <dd>
                {stats.sessions}{" "}
                <span className="text-muted-foreground text-xs">
                  ({stats.active_sessions} active)
                </span>
              </dd>
              <dt className="text-muted-foreground">events</dt>
              <dd>{stats.events}</dd>
            </dl>
          ) : (
            <p className="text-sm text-muted-foreground font-mono">
              backend unreachable
            </p>
          )}
        </CardContent>
      </Card>

      {/* ---------- Claude cost ---------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Claude cost</CardTitle>
        </CardHeader>
        <CardContent>
          {cost ? (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm font-mono">
              <dt className="text-muted-foreground">spent today</dt>
              <dd>${cost.today_usd.toFixed(4)}</dd>
              <dt className="text-muted-foreground">total spent</dt>
              <dd>${cost.total_usd.toFixed(4)}</dd>
              <dt className="text-muted-foreground">total calls</dt>
              <dd>{cost.total_calls}</dd>
              {Object.entries(cost.by_component).map(([k, v]) => (
                <div key={k} className="col-span-2 flex justify-between text-xs">
                  <span className="text-muted-foreground">{k}</span>
                  <span>${v.toFixed(4)}</span>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-sm text-muted-foreground font-mono">
              no cost data yet
            </p>
          )}
        </CardContent>
      </Card>
      </div>

      {/* ---------- System info (full width) ---------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Cpu className="h-4 w-4 text-safer-ice" />
            System
          </CardTitle>
        </CardHeader>
        <CardContent>
          {system ? (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-x-8 gap-y-4 text-sm font-mono">
              <dl className="space-y-1.5">
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
                  runtime
                </div>
                <Row k="safer version" v={system.safer_version} />
                <Row k="python" v={`${system.python_version} · ${system.platform}`} />
                <Row k="uptime" v={humanUptime(system.uptime_seconds)} />
                <Row k="db path" v={system.db_path} truncate />
                <Row k="db size" v={humanBytes(system.db_size_bytes)} />
              </dl>
              <dl className="space-y-1.5">
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
                  models
                </div>
                <Row k="judge" v={system.judge_model} />
                <Row k="haiku" v={system.haiku_model} />
                <Row k="policy compiler" v={system.policy_compiler_model} />
                <Row k="red-team" v={system.redteam_model} />
              </dl>
              <dl className="space-y-1.5">
                <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
                  claude usage
                </div>
                <Row k="opus calls" v={String(system.total_opus_calls)} />
                <Row k="haiku calls" v={String(system.total_haiku_calls)} />
                <Row
                  k="cache read ratio"
                  v={`${(system.cache_read_ratio * 100).toFixed(1)}%`}
                  emphasize={system.cache_read_ratio > 0.4}
                />
                <Row
                  k="tokens in / out"
                  v={`${system.total_tokens_in.toLocaleString()} / ${system.total_tokens_out.toLocaleString()}`}
                />
              </dl>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground font-mono">
              system info unavailable
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Row({
  k,
  v,
  truncate,
  emphasize,
}: {
  k: string;
  v: string;
  truncate?: boolean;
  emphasize?: boolean;
}) {
  return (
    <div className="flex items-start gap-3">
      <dt className="text-muted-foreground w-28 shrink-0">{k}</dt>
      <dd
        className={cn(
          "flex-1",
          truncate && "truncate",
          emphasize && "text-safer-ice"
        )}
        title={v}
      >
        {v}
      </dd>
    </div>
  );
}

/**
 * Controlled number input that commits on blur or Enter, so we don't
 * spam the backend on every keystroke.
 */
function NumberField({
  label,
  value,
  min,
  max,
  step,
  onCommit,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onCommit: (n: number) => void;
}) {
  const [draft, setDraft] = useState<string>(String(value));

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  const commit = () => {
    const n = parseInt(draft, 10);
    if (Number.isFinite(n) && n !== value) {
      const clamped = Math.max(min, Math.min(max, n));
      onCommit(clamped);
    } else {
      setDraft(String(value));
    }
  };

  return (
    <label className="flex items-center gap-3 text-xs font-mono">
      <span className="text-muted-foreground w-56 shrink-0">{label}</span>
      <input
        type="number"
        value={draft}
        min={min}
        max={max}
        step={step}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.currentTarget.blur();
          }
        }}
        className="rounded-md border border-border bg-background px-2 py-1 w-32 focus:outline-none focus:ring-1 focus:ring-safer-ice"
      />
    </label>
  );
}
