# CLAUDE.md

> This file is read by Claude Code at the start of every session. It tells Claude how to work on this repo.

## Project

**SAFER** — open-source Agent Control Plane. Monitors AI agents across their entire lifecycle (onboarding / pre-deploy / runtime / post-run), framework-agnostic, self-hosted, Claude-powered. Hackathon submission due 2026-04-26.

## What SAFER does (reference summary)

Four pillars × four lifecycle phases:

- **Observe** — live event feed, trace tree, heartbeat, per-step relevance
- **Evaluate** — Multi-Persona Judge (6 personas, dynamic routing per event, single Opus call), Quality Reviewer, Inspector (3 personas on code)
- **Control** — Gateway pre-call (PII regex + NL-compiled policies), SaferBlocked exception, 3 guard modes (Monitor/Intervene/Enforce), Red-Team Squad (manual, Claude Managed Agents trio)
- **Assure** — Session Report (per-session health card, deterministic), Compliance Pack (GDPR/SOC2/OWASP, PDF/HTML/JSON)

Lifecycle phases:
- **Onboarding** — Inspector runs 3 personas (Security, Compliance, Policy Warden) on agent code + AST + deterministic pattern scan, suggests policies
- **Pre-deploy** — Red-Team Squad runs (manual trigger) via 3 Claude Managed Agents (Strategist → Attacker → Analyst) with Plan B sub-agent fallback
- **Runtime** — Gateway + Judge + Per-step Haiku (at decision hooks only)
- **Post-run** — Quality Reviewer + Thought-Chain Reconstructor + Session Report

## Monorepo layout

```
packages/
  sdk/        — pip install safer-sdk (safer namespace)
  backend/    — FastAPI + asyncio + SQLite WAL
  dashboard/  — React + Vite + Tailwind + shadcn/ui
examples/     — demo agents (customer-support, code-analyst, vanilla-python)
docs/         — reference docs
roadmap/      — plan + phases
```

## Commands

| Task | Command |
|---|---|
| Install backend + SDK deps | `uv sync` |
| Run backend tests | `uv run pytest packages/backend packages/sdk` |
| Run backend dev | `uv run uvicorn safer_backend.main:app --reload` |
| Install dashboard deps | `cd packages/dashboard && npm install` (or `bun install` if you have it) |
| Run dashboard dev | `cd packages/dashboard && npm run dev` |
| Build dashboard | `cd packages/dashboard && npm run build` |
| Run everything (Docker) | `docker compose up` |

## Model routing (hard rules)

Three models in rotation. Each call-site picks the cheapest model that
meets its quality bar. Runtime-critical reasoning + cache-sharing
siblings stay on Opus; post-run / structured / clustering tasks drop to
Sonnet; simple per-step scoring uses Haiku.

| Call-site | Model | Rationale |
|---|---|---|
| Multi-Persona Judge | **Opus 4.7** | Runtime critical-path; 6-persona role-play; shared cache bucket with Inspector. |
| Inspector (3-persona code scan) | **Opus 4.7** | Deep code analysis; shared cache with Judge; fires once per agent onboarding. |
| Quality Reviewer | **Sonnet 4.6** | Session-end summary / goal-drift / hallucination; structured, not cache-sharing; Sonnet sufficient at 5× lower cost. |
| Thought-Chain Reconstructor | **Sonnet 4.6** | Forensic narrative reconstruction; Sonnet's strong suit. |
| Policy Compiler | **Sonnet 4.6** | NL → deterministic rule JSON; structured output task. |
| Red-Team Strategist | **Opus 4.7** | Creative attack planning; demo-critical quality. |
| Red-Team Attacker | **Opus 4.7** | Adversarial creativity; demo-critical quality. |
| Red-Team Analyst | **Sonnet 4.6** | Clustering + OWASP mapping; structured analysis. |
| Per-step Haiku score | **Haiku 4.5** | Fast relevance + escalate signal at decision hooks only. |
| Session Report aggregator | — | Pure Python, zero Claude calls. |

Per-call-site model can be overridden via environment variables
(`SAFER_JUDGE_MODEL`, `SAFER_QUALITY_MODEL`, `SAFER_RECON_MODEL`,
`SAFER_POLICY_MODEL`, `SAFER_REDTEAM_STRATEGIST_MODEL`,
`SAFER_REDTEAM_ATTACKER_MODEL`, `SAFER_REDTEAM_ANALYST_MODEL`). The
legacy `SAFER_REDTEAM_MODEL` still overrides all three Red-Team stages
when set, for backward compatibility.

Changing a call-site's default model requires updating this table + the
corresponding test expectations.

## Prompt cache (mandatory)

Every Opus call MUST use prompt caching:

```python
client.messages.create(
    model="claude-opus-4-7",
    system=[{
        "type": "text",
        "text": SYSTEM_PROMPT,  # ~3k tokens
        "cache_control": {"type": "ephemeral"}
    }],
    ...
)
```

Judge system prompt and Inspector persona prompt share the same cache (both are dual-mode personas). Don't split them.

## Temperature

**Do not pass `temperature` to `messages.create`.** Anthropic deprecated
this parameter on modern Claude models (Opus 4.7 returns a 400:
`` `temperature` is deprecated for this model `` — Haiku 4.5 likely the
same). All Opus/Haiku call sites in `safer_backend/` now omit the field
and rely on the model's default sampling. Deterministic behavior is
achieved via strict JSON output schemas + one-shot repair passes, not
the temperature knob. The Red-Team Attacker's previous "creative
temperature 0.8" is also gone; adversarial variance comes from the
attack prompts themselves.

## Persona routing (runtime Judge)

Hard rules, implemented in `safer_backend/router/persona_router.py`:

| Hook | Active personas |
|---|---|
| `before_tool_use` | Security, Compliance, Scope, Policy Warden |
| `on_agent_decision` | Scope, Policy Warden (+ Security, Trust if risk_hint) |
| `on_final_output` | All 6 personas |
| `on_agent_register` | None — onboarding event, consumed by the Agent Registry (not the Judge). The Inspector scan is triggered later via `POST /v1/agents/{id}/scan`. |
| Others | None (just logged, no Judge call) |

**Do not add new personas to runtime Judge without updating this table.**

## Database

- SQLite with WAL mode.
- Schema in `packages/backend/src/safer_backend/storage/schema.sql`.
- **Schema change = migration.** Update Pydantic models (`models/`) and run migration.
- Never mutate events in place; append-only.

## Snapshot scope (on_agent_register)

When `instrument()` fires `on_agent_register`, the SDK ships a gzip
snapshot of the agent's source. Default scope is:

1. **Workspace root** — the nearest `pyproject.toml` / `package.json`
   ancestor of the `instrument()` caller. If none is found, the caller's
   directory is used.
2. **Import graph walk** from the caller file, bounded to that
   workspace root. Files outside (site-packages, monorepo siblings,
   stdlib) are dropped silently.
3. `__init__.py` of every ancestor package gets pulled in too.
4. Fallback: if the walk produces zero files, fall back to a recursive
   `.py` walk of the workspace root.

Override knobs on `instrument()`:
- `scan_mode="imports" | "directory" | "explicit"`
- `include=[glob, ...]` — appends files (also accepts non-`.py`, e.g.,
  `prompts/**/*.md`)
- `exclude=[glob, ...]` — drops paths
- `project_root=...` — override workspace root detection

## Testing

- `pytest` for backend + SDK.
- Each adapter gets a test that emits the 9 runtime hooks and verifies they reach the backend. `on_agent_register` is the 10th hook (onboarding-phase, emitted once per process by `instrument()`).
- Judge engine test: prompt cache hit rate > 80% on the 2nd+ call.

## Commit style

- `feat(sdk): ...` — new feature
- `fix(judge): ...` — bug fix
- `refactor(backend): ...` — refactor without behavior change
- `docs: ...` — docs only
- `test: ...` — test only
- `chore: ...` — config, deps, etc.

Scopes: `sdk`, `backend`, `dashboard`, `judge`, `inspector`, `gateway`, `redteam`, `policy_studio`, `session_report`, `compliance`, `adapters`, `roadmap`, `infra`.

## Don't-do list

1. **Don't add other LLM providers.** Claude-only. No OpenAI / Gemini / Llama calls anywhere.
2. **Model choice per call-site is not ad-hoc.** Defaults are defined in the Model routing table above; change them only with the test + dashboard implications in mind. Per-request overrides use the documented env vars.
3. **Don't add workspace isolation.** Out of scope — the Gateway already restricts tool use.
4. **Don't add Red-Team continuous mode.** Always a manual button.
5. **Don't let Judge run on every event.** Router filters strictly; see persona routing table.
6. **Don't call Claude in Session Report aggregator.** It's pure Python; 0 Claude calls by design.
7. **Don't hardcode API keys.** `.env` file only, never committed.
8. **Don't commit the SQLite database.** `*.db` is in `.gitignore`.
9. **Don't skip prompt cache.** Every Opus call uses it; cache hit is logged.
10. **Don't use Next.js.** React + Vite is the choice; don't migrate.
11. **All code, comments, docs, commits, and identifiers in this repo are in English.** SAFER is a global open-source project.
12. **Don't pass `temperature` to `messages.create`.** Anthropic deprecated it on modern Claude models (400 on Opus 4.7). All call sites omit the param and use the model default.

## Key decisions (locked)

- **Name:** SAFER + "Agent Control Plane" tagline
- **Frontend:** React + Vite (NOT Next.js)
- **Frameworks at MVP:** 2 bundled (Claude SDK + LangChain) + 1 partial (OpenAI) + 3 stubs (Google ADK, Bedrock, CrewAI) + Custom SDK (`safer.track_event()`)
- **Red-Team:** ALWAYS manual button. No continuous mode. Plan B sub-agent fallback paralel.
- **6 personas:** Stay as 6, but dynamic routing per event (NOT all 6 every time)
- **Policy Warden + Scope Enforcer:** Live, every critical step
- **Ethics Reviewer:** Only on `on_final_output`
- **Per-step Haiku:** Only at decision hooks (before_llm_call, before_tool_use, on_agent_decision). NOT on observe-only hooks.
- **Session Report:** Deterministic Python aggregator, 0 Claude calls
- **Workspace isolation:** Out of scope (Gateway restricts enough)
- **Credential masking:** Regex in SDK transport + backend log layer
- **Inspector:** 3-persona single Opus call (NOT separate Sonnet prompt security)
- **Database:** SQLite WAL, append-only events, schema change = migration
