# SAFER — Agent Control Plane

> **Open-source agent control plane for the entire AI agent lifecycle.**
> Onboarding → Pre-deploy → Runtime → Post-run.
> Self-hosted · framework-agnostic · Claude-powered.

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-185%20passing-brightgreen.svg)](#testing)
[![Claude Hackathon 2026](https://img.shields.io/badge/Claude%20Hackathon-2026-purple.svg)](https://www.cerebralvalley.ai/)

AI agents are shipping faster than anyone can audit them. SAFER gives
you a single place to **observe, evaluate, control, and assure** every
agent your team runs — in every phase of its lifecycle, behind your own
VPC, with one line of instrumentation and a dashboard your auditors can
actually use.

---

## Table of contents

- [Why SAFER](#why-safer)
- [Features at a glance](#features-at-a-glance)
- [Dashboard walk-through](#dashboard-walk-through)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Using SAFER in your agent](#using-safer-in-your-agent)
- [Framework matrix](#framework-matrix)
- [The 9-hook lifecycle contract](#the-9-hook-lifecycle-contract)
- [The six personas (dynamic routing)](#the-six-personas-dynamic-routing)
- [How SAFER uses Claude](#how-safer-uses-claude)
- [Examples](#examples)
- [Configuration](#configuration)
- [Development](#development)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Security model](#security-model)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## Why SAFER

Agent frameworks multiply. Each one ships its own callback surface,
its own trace format, its own half-finished safety story. The result
for any team running more than one agent in production:

- No consistent view of what the agents actually did.
- No consistent way to enforce a policy across them.
- No consistent report you can hand to an auditor.

SAFER is opinionated about the shape of that missing layer:

1. **Framework-agnostic contract.** Nine lifecycle hooks —
   `on_session_start`, `before/after_llm_call`, `before/after_tool_use`,
   `on_agent_decision`, `on_final_output`, `on_session_end`, `on_error`
   — that every adapter normalises into.
2. **Claude as the reasoning engine.** Every judgement call that needs
   intent, context, or nuance is a cached Opus 4.7 call. Everything
   deterministic (pattern scanning, policy evaluation, report
   aggregation) is pure Python.
3. **Self-hosted.** SQLite WAL + FastAPI backend + React/Vite
   dashboard, all behind one `docker compose up`. No data leaves your
   machine except your own Anthropic API calls.
4. **Built for the full lifecycle — not just runtime.** Four pillars
   (Observe, Evaluate, Control, Assure) across four lifecycle phases
   (Onboarding, Pre-deploy, Runtime, Post-run).

---

## Features at a glance

|  | **Onboarding** | **Pre-deploy** | **Runtime** | **Post-run** |
|---|---|---|---|---|
| **Observe** | Agent registry with version + framework | — | Live event feed, per-step Haiku relevance score, CRITICAL pulse | Session timeline + trace tree + Thought-Chain narrative |
| **Evaluate** | **Inspector** — AST scan + 12 deterministic patterns + 3-persona Opus review, single call | — | **Multi-Persona Judge** — 6 personas with dynamic routing, single Opus call per hook | **Quality Reviewer** — task completion, hallucination, efficiency, goal-drift |
| **Control** | Auto-suggested policies from Inspector findings | — | **Gateway** — PII regex + NL-compiled policies (3 guard modes: Monitor / Intervene / Enforce), `SaferBlocked` exception | Verdict + block signals broadcast to dashboard |
| **Assure** | Risk score + OWASP-aligned findings | **Red-Team Squad** — 3-stage adversarial evaluation (Strategist → Attacker → Analyst) mapped to OWASP LLM Top 10 | Block Moment toast with explainability drawer | **Session Report** — 7-category health card + **Compliance Pack** (GDPR / SOC 2 / OWASP LLM) in PDF / HTML / JSON |

Everything above is wired end-to-end in the current release and
verified by the 185-test suite.

---

## Dashboard walk-through

The `/overview` page is the home; each page below is a full-fat
feature, not a stub.

| Route | What it shows |
|---|---|
| `/overview` | 4 KPIs (agents / sessions / events / spend today) + live recent-activity strip. |
| `/live` | WebSocket event stream, agent / hook / risk filters, CRITICAL-pulse rows, Block Moment toast + PersonaDrawer with every persona verdict for the selected event. |
| `/agents` | Inspector — paste an agent's source, Gauge animates to the deterministic risk score, pattern matches + AST summary + policy suggestions (copy-to-clipboard into Policy Studio). |
| `/sessions` | Every session as a row with started_at, duration, step count, overall health, and cost — click to open the detail page. |
| `/sessions/:id` | Full health card (animated Gauge + 7 category bars + top findings + OWASP mini-grid + Red-Team summary), Thought-Chain narrative streaming, vertical Timeline, and a nested LLM ↔ tool-call Trace Tree. |
| `/policies` | Policy Studio — compose a policy in English, Opus 4.7 compiles it into a deterministic Gateway rule with test cases, activate with one click. Left column lists every active policy (including the four built-ins). |
| `/quality` | Rolling-health sparkline, per-session category fingerprint table, top concern per session. |
| `/redteam` | Run Red-Team modal (target system prompt + tools + mode), animated phase strip (Strategist → Attacker → Analyst), OWASP 10-row grid, expandable attempts list, export JSON. |
| `/reports` | Compliance Pack — GDPR / SOC 2 / OWASP LLM across any date range, as HTML (in-page preview + Print-to-PDF), PDF (WeasyPrint), or JSON. |
| `/settings` | Backend status, Claude cost summary, guard-mode configuration notes. |

> Screenshots will be added under `docs/screenshots/` during the final
> submission polish.

---

## Architecture

```
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                          YOUR AGENT (any framework)                     │
   │                                                                         │
   │     ┌──────────────────┐           safer.instrument()                   │
   │     │  framework hook  │◀───────── bundled adapter                      │
   │     └────────┬─────────┘                                                │
   └──────────────┼──────────────────────────────────────────────────────────┘
                  │ 9-hook lifecycle payload (pydantic)
                  ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                            SAFER SDK                                    │
   │   transport (WebSocket + HTTP fallback, batched)   credential masking   │
   └──────────────┬──────────────────────────────────────────────────────────┘
                  │ ndjson-over-WebSocket
                  ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                          SAFER BACKEND                                  │
   │                                                                         │
   │   Ingestion ─▶ Router ─▶ ┌────────────────────────────────────────┐    │
   │                          │  Gateway   (PII regex + policies,      │    │
   │                          │            deterministic, ≤ 20 ms)     │    │
   │                          │  Haiku pre-step score (decision hooks) │    │
   │                          │  Multi-Persona Judge (Opus 4.7, 1 call │    │
   │                          │            with dynamic persona set,   │    │
   │                          │            cache hit-rate > 80 %)      │    │
   │                          └────────────────────────────────────────┘    │
   │                          SQLite WAL  · append-only events              │
   │                                                                         │
   │   on_session_end ─▶ Quality Reviewer (Opus)                            │
   │                  ─▶ Thought-Chain Reconstructor (Opus, HIGH+)          │
   │                  ─▶ Session Report Aggregator (pure Python, 0 Claude)  │
   │                                                                         │
   │   user-triggered ─▶ Inspector  (AST + 12 patterns + 3-persona Opus)    │
   │                  ─▶ Red-Team   (Strategist → Attacker → Analyst,       │
   │                                  sub-agent fallback, always manual)    │
   │                  ─▶ Compliance Pack (Jinja2 + WeasyPrint)              │
   └────────────────────────┬────────────────────────────────────────────────┘
                            │ REST + /ws/stream
                            ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                        SAFER DASHBOARD                                  │
   │         React · Vite · Tailwind · 10 routes · dark-first                │
   └─────────────────────────────────────────────────────────────────────────┘
```

Hard rules enforced by the architecture (see `CLAUDE.md` for the full
list):

- **Every Opus call is cached.** `cache_control: ephemeral` on the
  system prompt, temperature=0 for anything classification-like, so
  the second onward call hits the 5-minute cache.
- **Judge is strict.** Runs only on three hooks (`before_tool_use`,
  `on_agent_decision`, `on_final_output`) and only with the personas
  the event actually needs.
- **Session Report aggregator is pure Python.** Zero Claude calls at
  aggregation time; the LLM work is already done by the Judge /
  Quality / Reconstructor.
- **Red-Team is always manual.** No continuous mode. The button
  triggers one run; the orchestrator falls back from Managed Agents to
  sub-agent transparently.

---

## Quick start

### Docker (preferred)

```bash
git clone https://github.com/hashtagemy/safer.git
cd safer
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY (and anything else you care about)

docker compose up --build
```

That's it. The backend comes up on `http://localhost:8000` (with a
`/health` endpoint and an integrated WebSocket at `/ws/stream`), and
the dashboard on `http://localhost:5173`.

> **Claude Managed Agents.** The onboarding-phase Inspector runs as a
> Claude Managed Agent by default (beta header
> `managed-agents-2026-04-01`), with a shared `safer-inspector-knowledge`
> memory store that persists patterns across scans. No extra setup is
> required — same `docker compose up`. If your API key doesn't have
> Managed Agents beta access the Inspector falls back to the legacy
> single-call path transparently. To verify beta access ahead of a
> run: `uv run python scripts/check_managed_agents_access.py`.

### Seeding demo data

Want a dashboard full of realistic data without running a live agent?

```bash
docker compose exec backend uv run python /app/scripts/seed_demo.py
```

That script creates two agents, five sessions (clean, PII leak, prompt
injection, tool loop, mixed analyst), and one completed Red-Team run
— all deterministic, zero Claude calls, and the Session Reports are
pre-generated so every page has content on first load.

### Tearing down

```bash
docker compose down -v    # -v also wipes the named safer_db volume
```

---

## Using SAFER in your agent

### The one-liner

```python
from safer import instrument

instrument()   # idempotent; detects the frameworks you have installed
```

That's the whole integration for any framework with a bundled adapter.

### With the Anthropic Claude Agent SDK

```python
from anthropic import Anthropic
from safer import instrument
from safer.adapters.claude_sdk import wrap_anthropic

instrument()
client = Anthropic()
agent = wrap_anthropic(client, agent_id="support", agent_name="Customer Support")

agent.start_session(context={"user": "alice"})
response = agent.messages.create(model="claude-opus-4-7", messages=[...])
#   → before_llm_call + after_llm_call emit automatically

agent.before_tool_use("get_order", {"id": 123})
result = get_order(123)
agent.after_tool_use("get_order", result)

agent.final_output("Your order has shipped.")
agent.end_session(success=True)
```

### With LangChain / LangGraph

```python
from safer import instrument
from safer.adapters.langchain import SaferCallbackHandler

instrument()
handler = SaferCallbackHandler(agent_id="code_analyst", agent_name="Code Analyst")

agent_executor.invoke(
    {"input": "..."},
    config={"callbacks": [handler]},
)
```

`SaferCallbackHandler` maps every LangChain callback (`on_chain_start`,
`on_llm_start`, `on_tool_start`, `on_agent_action`, `on_agent_finish`,
the three `*_error` hooks, etc.) onto the 9 SAFER hooks — nothing extra
for you to wire.

### With OpenAI Agents SDK (partial)

```python
from openai import OpenAI
from safer import instrument
from safer.adapters.openai_agents import wrap_openai

instrument()
client = wrap_openai(OpenAI(), agent_id="assistant")

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "hi"}],
)
#   → before_llm_call + after_llm_call + on_error automatic.
#   Tool / decision / final hooks are emitted via wrap_openai helpers or
#   safer.track_event() for now.
```

### Vanilla Python (no framework)

```python
from safer import Hook, instrument, track_event

instrument()
track_event(Hook.ON_SESSION_START, {"agent_name": "my-agent"},
            session_id="sess_1", agent_id="my-agent")
track_event(Hook.BEFORE_LLM_CALL, {"model": "claude-opus-4-7",
                                   "prompt": "..."}, ...)
# ...and so on for each of the 9 hooks.
```

See [`examples/vanilla-python`](examples/vanilla-python) for a
complete, 60-line walkthrough.

---

## Framework matrix

| Framework | Support | How |
|---|---|---|
| Anthropic Claude Agent SDK | ✅ **Bundled** | `wrap_anthropic(client, agent_id=...)` — all 9 hooks automatic |
| LangChain / LangGraph | ✅ **Bundled** | `SaferCallbackHandler` — pass via `callbacks=[...]` (9 hooks) |
| OpenAI Agents SDK | 🔶 **Partial** | `wrap_openai(client, agent_id=...)` — `before/after_llm_call` + `on_error` automatic; tool / decision hooks via `safer.track_event()` |
| Google ADK | 🔶 Beta stub | `wrap_adk(...)` is an import-safe no-op + warning; use `safer.track_event()` today |
| AWS Bedrock Agents | 🔶 Beta stub | `wrap_bedrock(...)` is an import-safe no-op + warning; use `safer.track_event()` today |
| CrewAI | 🔶 Beta stub | `wrap_crew(...)` is an import-safe no-op + warning; use `safer.track_event()` today |
| AWS Strands | 🟡 OTel | Native OTel, works through the OTLP ingestion shim |
| LlamaIndex / AutoGen / anything else | 🔵 Custom SDK | 10 lines via `safer.track_event(Hook.*, payload)` — see `examples/vanilla-python` |

### Adding your own framework

1. **Emit events manually** — `safer.track_event(Hook.*, payload)` at
   each lifecycle point in your run loop.
2. **Write an adapter** — add
   `packages/sdk/src/safer/adapters/<name>.py`, wrap or subclass the
   framework's native callback surface, translate events to the 9
   hooks. The [LangChain
   adapter](packages/sdk/src/safer/adapters/langchain.py) is the
   reference implementation (≈ 350 lines).

---

## The 9-hook lifecycle contract

Every adapter converts its framework's native events into exactly these
payloads. Pydantic models live in
[`packages/sdk/src/safer/events.py`](packages/sdk/src/safer/events.py).

| Hook | Emitted when | Used by |
|---|---|---|
| `on_session_start` | Agent begins | Session record, dashboard live feed |
| `before_llm_call` | Before each LLM request | Haiku pre-step, cost tracking |
| `after_llm_call` | After each LLM response | Cost + latency + token accounting |
| `before_tool_use` | Before a tool runs | **Gateway**, **Judge** (full persona set), Haiku pre-step |
| `after_tool_use` | After a tool returns | Loop detection input, dashboard |
| `on_agent_decision` | Agent picks an action | **Judge** (Scope + Policy Warden), Haiku pre-step |
| `on_final_output` | Agent finishes the turn | **Judge** (all six personas), quality cues |
| `on_session_end` | Session completes | Triggers **Session Report** pipeline |
| `on_error` | Any adapter-level error | Dashboard, audit trail |

---

## The six personas (dynamic routing)

The Multi-Persona Judge is **one Opus 4.7 call per hook** with a cached
~3 KB system prompt that holds every persona's definition. Only the
personas the event actually needs are listed in `active_personas`, so
the model produces verdicts only for them.

| Persona | Focus | When it's active |
|---|---|---|
| **Security Auditor** | Attack surface, injections, tool abuse | `before_tool_use`, `on_final_output`, any `risk_hint ≥ MEDIUM` |
| **Compliance Officer** | PII, GDPR / SOC 2 / HIPAA signals | `before_tool_use`, `on_final_output` |
| **Trust Guardian** | Hallucinations, false-success claims | `on_final_output` only (behavioral) |
| **Scope Enforcer** | Goal drift, loops, unnecessary steps | `before_tool_use`, `on_agent_decision`, `on_final_output` |
| **Ethics Reviewer** | Bias, toxic output, harmful content | `on_final_output` only |
| **Policy Warden** | User-compiled policies (Policy Studio) | Every critical hook |

The routing logic is deterministic — see
[`router/persona_router.py`](packages/backend/src/safer_backend/router/persona_router.py).

---

## How SAFER uses Claude

> This is a Claude-powered product end to end. The list below is
> exhaustive — every Claude call SAFER makes is here.

| Feature | Model | Temperature | Prompt cached | Why |
|---|---|---|---|---|
| Multi-Persona Judge | Opus 4.7 | 0 | ✅ `ephemeral` | Deterministic classification; one call covers 2–6 personas |
| Inspector — 3-persona review | Opus 4.7 | 0 | ✅ | Same system prompt as the Judge — cache hits across both |
| Quality Reviewer | Opus 4.7 | 0 | ✅ | Once per session (on `on_session_end`) |
| Thought-Chain Reconstructor | Opus 4.7 | 0 | ✅ | Auto on HIGH+ verdict, manual otherwise |
| Policy Compiler | Opus 4.7 | 0 | ✅ | NL → deterministic rule_json + test cases |
| Red-Team Strategist | Opus 4.7 | 0 | ✅ | One call; seed bank + target → tailored attacks |
| Red-Team Attacker | Opus 4.7 | 0.8 | ✅ | Creative adversary, one call per attack |
| Red-Team Analyst | Opus 4.7 | 0 | ✅ | Clusters attempts into findings + OWASP map |
| Per-step Haiku score | Haiku 4.5 | 0 | — | Fast relevance + escalate signal at decision hooks only |
| Session Report aggregator | — | — | — | **Pure Python, zero Claude calls** |

Hard rules (also in [`CLAUDE.md`](CLAUDE.md)):

- **Claude-only.** No OpenAI / Gemini / Llama calls anywhere in the
  product code. Other frameworks plug in through adapters; the reasoning
  engine is always Claude.
- **Opus 4.7 and Haiku 4.5 only.** Sonnet 4.6 was deliberately removed
  in v3.
- **Every Opus call uses prompt caching.** Cache hit rate > 80 % on
  the second call onward; verified by the Judge engine's unit tests.
- **Temperature=0 for every classifier; 0.8 only for the adversarial
  Attacker.**

---

## Examples

| Path | Framework | Demo |
|---|---|---|
| [`examples/customer-support`](examples/customer-support) | Anthropic Claude Agent SDK | Customer-support bot with intentionally risky tools — the canonical demo for the 3-minute video |
| [`examples/code-analyst`](examples/code-analyst) | LangChain + `langchain-anthropic` | Tool-calling agent that reads / greps / AST-scans this repo; shows `SaferCallbackHandler` end-to-end |
| [`examples/vanilla-python`](examples/vanilla-python) | None (custom SDK) | Minimal 60-line manual instrumentation using `safer.track_event()` |

Each example has its own `README.md` with prereqs and run commands.

---

## Configuration

All configuration is via environment variables; `.env.example` is the
canonical template.

| Variable | Default | What |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for any Claude-backed feature (Judge, Inspector persona review, Policy Compiler, Red-Team, Quality, Reconstructor). Event ingestion + pattern scans + aggregator work without it. |
| `SAFER_API_URL` | `http://localhost:8000` | Where the SDK sends events. |
| `SAFER_WS_URL` | `ws://localhost:8000/ingest` | WebSocket ingestion endpoint (SDK). |
| `SAFER_DB_PATH` | `./safer.db` | SQLite WAL file. In Docker we mount a named volume at `/app/data/safer.db`. |
| `SAFER_GUARD_MODE` | `intervene` | `monitor` (log only), `intervene` (block CRITICAL), `enforce` (block every hit). |
| `SAFER_JUDGE_ENABLED` | `auto` | `auto` (on iff `ANTHROPIC_API_KEY` set) · `on` · `off`. |
| `SAFER_JUDGE_MODEL` | `claude-opus-4-7` | Override with another supported Claude model. |
| `SAFER_JUDGE_MAX_TOKENS` | `2000` | Max output tokens for the Judge. |
| `RED_TEAM_MODE` | `subagent` | `managed` (Claude Managed Agents) or `subagent` (plain Opus). Managed → subagent fallback is automatic. |
| `VITE_BACKEND_URL` | `http://localhost:8000` | Dashboard → backend REST base. |
| `VITE_WS_URL` | `ws://localhost:8000` | Dashboard → backend WebSocket base. |

---

## Development

### Prerequisites

- Python 3.11+ (3.11 recommended)
- Node 20+ (or Bun 1+)
- [uv](https://github.com/astral-sh/uv) for the Python workspace
- Docker (optional but strongly recommended for the full stack)
- WeasyPrint's native libraries (cairo, pango, gdk-pixbuf) if you want
  PDF export locally — the Docker image installs them for you.

### Backend + SDK

```bash
uv sync
uv run uvicorn safer_backend.main:app --reload --port 8000
```

### Dashboard

```bash
cd packages/dashboard
npm install           # or `bun install`
npm run dev           # Vite on :5173
npm run build         # produces dist/ (TS build + Vite bundle)
```

### Linting

Ruff config lives at the workspace root (`[tool.ruff]` in
`pyproject.toml`). Run it with:

```bash
uv run ruff check .
uv run ruff format .
```

---

## Testing

```bash
uv run pytest              # 185 tests, 1 skipped (needs langchain-core)
```

Test coverage:

- **SDK** — event models, transport batching + backpressure, credential
  masking (7 patterns), Claude SDK adapter, LangChain adapter
  (fallback + full flow + error), OpenAI partial adapter, stubs.
- **Backend** — all ingestion paths, Judge engine (prompt cache, JSON
  repair), persona routing, Gateway (PII regex + 3 guard modes),
  Inspector (AST, 12 patterns, persona review), Policy Studio
  (compile + activate + gateway integration), Session Report
  (aggregator across scenarios, OWASP map, cost summary, orchestrator,
  API), Red-Team (seed bank, each stage, orchestrator success + fail,
  API), Compliance Pack (loader, all three templates, HTML/JSON/PDF
  rendering, API 400 / 501 paths), sessions list + events endpoint.

No tests require a live Anthropic API key. Each Claude-powered
component exposes a `set_client()` injection point that tests use with
small fake clients.

---

## Project layout

```
safer/
├─ CLAUDE.md                    # hard rules for Claude Code (model routing,
│                               #   don't-do list, prompt-cache requirement)
├─ README.md                    # this file
├─ pyproject.toml               # uv workspace root
├─ uv.lock
├─ docker-compose.yml
├─ Dockerfile.backend
├─ Dockerfile.dashboard
├─ packages/
│  ├─ sdk/                      # pip install safer-sdk
│  │  └─ src/safer/
│  │     ├─ instrument.py       # one-liner entry point
│  │     ├─ client.py           # singleton + event loop + sequence counter
│  │     ├─ transport.py        # async WS + HTTP fallback, batched
│  │     ├─ events.py           # 9 pydantic payload models
│  │     ├─ masking.py          # 7 credential regexes
│  │     ├─ exceptions.py       # SaferBlocked
│  │     └─ adapters/
│  │        ├─ claude_sdk.py    # ✅ Bundled
│  │        ├─ langchain.py     # ✅ Bundled
│  │        ├─ openai_agents.py # 🔶 Partial
│  │        ├─ google_adk.py    # 🔶 Stub
│  │        ├─ bedrock.py       # 🔶 Stub
│  │        └─ crewai.py        # 🔶 Stub
│  ├─ backend/                  # FastAPI + asyncio + SQLite WAL
│  │  └─ src/safer_backend/
│  │     ├─ main.py             # app + routers
│  │     ├─ ingestion/          # WebSocket + HTTP event ingest
│  │     ├─ router/             # event routing, persona selection,
│  │     │                      #   Judge dispatch, Haiku pre-step,
│  │     │                      #   session-end hooks
│  │     ├─ gateway/            # PII regex + policy engine + guard modes
│  │     ├─ judge/              # Opus 4.7 multi-persona engine + cost tracker
│  │     ├─ inspector/          # AST + patterns + persona review + API
│  │     ├─ policy_studio/      # NL → rule compiler + API
│  │     ├─ quality/            # Quality Reviewer
│  │     ├─ reconstructor/      # Thought-Chain Reconstructor
│  │     ├─ session_report/     # deterministic aggregator + API
│  │     ├─ redteam/            # seed bank + Strategist/Attacker/Analyst + API
│  │     ├─ compliance/         # Jinja2 + WeasyPrint reports
│  │     ├─ storage/            # schema.sql + DAOs + migration runner
│  │     ├─ models/             # pydantic models (verdicts, findings, ...)
│  │     ├─ ws_broadcaster.py   # dashboard live fan-out
│  │     └─ sessions_api.py     # /v1/sessions list + events
│  └─ dashboard/                # React + Vite + Tailwind + shadcn-style
│     └─ src/
│        ├─ App.tsx             # 10 routes
│        ├─ pages/              # one file per route
│        ├─ components/         # Card / Badge / PersonaDrawer /
│        │                      #   BlockMomentToast / Timeline /
│        │                      #   TraceTree / NarrativeStreaming / Gauge
│        └─ lib/
│           ├─ api.ts           # fetchJSON + env constants
│           ├─ ws.ts            # useSaferRealtime hook (event + verdict
│           │                   #   + prestep + block + redteam_phase)
│           └─ sessionTypes.ts  # TS mirror of backend pydantic models
├─ examples/
│  ├─ customer-support/         # Anthropic Agent SDK demo
│  ├─ code-analyst/             # LangChain demo
│  └─ vanilla-python/           # custom-SDK demo
├─ scripts/
│  └─ seed_demo.py              # deterministic 5-scenario seed
└─ docs/
   └─ demo/
      ├─ smoke.md               # cold-clone smoke checklist
      └─ script.md              # 3-minute demo script
```

---

## Security model

- **Credential masking** is applied in two places — in the SDK
  transport before events leave the agent process, and in the backend
  logging layer before anything is persisted or broadcast. Seven regex
  patterns cover Anthropic, OpenAI, AWS, GitHub, Slack, generic bearer
  tokens, and PEM-encoded private keys.
- **`.env` is gitignored.** SQLite database files (`*.db*`) are
  gitignored. No demo credentials travel with the repository.
- **Self-hosted by design.** SAFER never sends data to a hosted
  control plane. All Claude API calls go directly from your backend
  with your own `ANTHROPIC_API_KEY`.
- **Red-Team Squad is defensive.** It is user-triggered, agent-scoped,
  OWASP-aligned, and produces auditable findings — consistent with
  Anthropic's Usage Policy for security research. There is no
  continuous mode.
- **Gateway modes** (`monitor` / `intervene` / `enforce`) let you
  ramp enforcement at your own pace; `SaferBlocked` is raised in the
  agent process so the agent can handle it gracefully.

---

## Roadmap

**v0.1** (this release — Claude Hackathon 2026 submission):

- All four pillars × four lifecycle phases live.
- Bundled adapters for Claude SDK and LangChain.
- Partial OpenAI Agents adapter; beta stubs for Google ADK, Bedrock,
  CrewAI.
- Policy Studio, Inspector, Red-Team, Session Report, Compliance Pack.
- Self-hosted dashboard with 10 fully-featured pages.

**v0.2 (planned):**

- Full Google ADK, Bedrock, and CrewAI adapters.
- Managed Agents backend for Red-Team once the API is widely available.
- Grafana / OTel exporter for org-wide rollups.
- Policy Studio "diff preview" — see which past events the new policy
  would have changed.

---

## License

Apache 2.0 — see [LICENSE](LICENSE). SAFER is MIT/Apache-compatible.
All third-party dependencies are permissively licensed.

---

## Acknowledgments

- Anthropic for Claude Opus 4.7 + Haiku 4.5, prompt caching, and the
  Agent SDK.
- The LangChain team for a callback surface that made the 9-hook
  mapping straightforward.
- The OWASP LLM Top 10 working group for the category taxonomy SAFER
  compiles its reports against.

Built for **Claude Hackathon 2026**.
