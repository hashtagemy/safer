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

- **Opus 4.7** — Judge (6 personas, dual-mode), Inspector (3-persona code scan), Thought-Chain Reconstructor, Quality Reviewer, Policy Compiler, Red-Team trio.
- **Haiku 4.5** — Per-step scoring (relevance + escalate combined, at decision hooks only), Gateway borderline (stretch).
- **Sonnet 4.6** — **NOT USED.** Removed in v3. Do not reintroduce; if you think a task needs Sonnet, open an issue first.

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

- Judge / Inspector / Reconstructor / Quality / Policy compiler: **temperature=0** (deterministic).
- Red-Team Attacker: creative temperature OK (0.7-1.0).
- Haiku per-step: temperature=0.

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
2. **Don't reintroduce Sonnet 4.6.** Deliberately removed in v3.
3. **Don't add workspace isolation.** Out of scope — the Gateway already restricts tool use.
4. **Don't add Red-Team continuous mode.** Always a manual button.
5. **Don't let Judge run on every event.** Router filters strictly; see persona routing table.
6. **Don't call Claude in Session Report aggregator.** It's pure Python; 0 Claude calls by design.
7. **Don't hardcode API keys.** `.env` file only, never committed.
8. **Don't commit the SQLite database.** `*.db` is in `.gitignore`.
9. **Don't skip prompt cache.** Every Opus call uses it; cache hit is logged.
10. **Don't use Next.js.** React + Vite is the choice; don't migrate.
11. **All code, comments, docs, commits, and identifiers in this repo are in English.** SAFER is a global open-source project.

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
