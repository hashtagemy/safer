# SAFER — Agent Control Plane

> **Open-source agent control plane for the entire AI agent lifecycle.**
> Onboarding → Pre-deploy → Runtime → Post-run.
> Self-hosted · framework-agnostic · Claude-powered.

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-357%20passing-brightgreen.svg)](#testing)
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
verified by the 357-test suite.

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

### What `instrument()` actually does

```python
from safer import instrument
instrument(agent_id="support", agent_name="Support")
```

`instrument()` boots the SAFER runtime (transport + backend connection),
emits a one-shot `on_agent_register` event carrying a gzip snapshot of
your agent's source (for the Inspector scan), and tags the agent with
whichever framework it detects in your environment.

**`instrument()` does not bridge the 9 lifecycle hooks on its own.** The
per-framework adapter (one more line in your code) does that. And as a
convenience, every bundled adapter calls `ensure_runtime(...)` in its
constructor — so if you don't need to customize the runtime, you can
skip `instrument()` entirely and just write the adapter line.

### Two-line integration per framework

Whichever framework you're on, there's exactly one adapter line to
add. Copy-paste from the table below into your own code; no other
glue is required.

#### Google ADK — Runner plugin

```python
from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from safer.adapters.google_adk import SaferAdkPlugin

agent = LlmAgent(model="gemini-2.5-pro", ...)
runner = InMemoryRunner(
    agent=agent,
    app_name="repo_analyst",
    plugins=[SaferAdkPlugin(agent_id="repo_analyst",
                             agent_name="Repo Analyst")],
)
```

Native layer: ADK `BasePlugin` (Google-recommended for monitoring).
All 9 SAFER hooks emit automatically.

#### AWS Strands — Agent hook provider

```python
from strands import Agent
from strands.models.anthropic import AnthropicModel
from safer.adapters.strands import SaferHookProvider

agent = Agent(
    model=AnthropicModel(model_id="claude-opus-4-7"),
    tools=[...],
    hooks=[SaferHookProvider(agent_id="system_diag",
                              agent_name="System Diagnostic")],
)
```

Native layer: Strands `HookProvider` (the modern replacement for
Strands' older callback handler mechanism). All 9 SAFER hooks emit
automatically.

#### LangChain / LangGraph — Callback handler

```python
from safer.adapters.langchain import SaferCallbackHandler

handler = SaferCallbackHandler(agent_id="code_analyst",
                                agent_name="Code Analyst")
agent_executor.invoke({"input": "..."},
                      config={"callbacks": [handler]})
```

Native layer: LangChain `BaseCallbackHandler`. All 9 SAFER hooks emit
automatically — no manual helpers required.

#### Anthropic (raw SDK) — `SaferAnthropic` subclass (recommended)

```python
from safer.adapters.claude_sdk import SaferAnthropic

client = SaferAnthropic(agent_id="support", agent_name="Support")
client.messages.create(model="claude-opus-4-7", messages=[...])
```

`SaferAnthropic` is a real `anthropic.Anthropic` subclass — every API
the SDK exposes still works.  All nine SAFER hooks fire automatically:
LLM-call pair, tool-use auto-detection from `Message.content`, and
`after_tool_use` synthesised on the next request when its `messages`
array carries a matching `tool_result` block.  Use `SaferAsyncAnthropic`
for `async`/`await` code.

#### Anthropic (raw SDK) — `wrap_anthropic` (existing client wrapper)

If you already have an `Anthropic()` instance you want to keep:

```python
from anthropic import Anthropic
from safer.adapters.claude_sdk import wrap_anthropic

client = wrap_anthropic(Anthropic(), agent_id="support", agent_name="Support")
client.messages.create(model="claude-opus-4-7", messages=[...])
```

Same automatic hook coverage as `SaferAnthropic` (tool_use detection +
tool_result pairing).  Detects `AsyncAnthropic` automatically and
returns an async-aware proxy.

#### OpenAI (raw SDK) — `wrap_openai`

```python
from openai import OpenAI
from safer.adapters.openai_client import wrap_openai

client = wrap_openai(OpenAI(), agent_id="assistant", agent_name="Assistant")
client.chat.completions.create(model="gpt-4o", messages=[...])
```

Only LLM endpoints are instrumented — `chat.completions.create/parse`,
`responses.create`, plus their `with_raw_response` and
`with_streaming_response` siblings.  Embeddings, files, images, batches
pass through unchanged.  Streaming responses are accumulated chunk-by-
chunk so `after_llm_call` carries real tool-call + usage data; tool
calls in the response auto-emit `before_tool_use`, paired with
`after_tool_use` when the next request carries the `role="tool"` reply.
`AsyncOpenAI` detected automatically.

#### OpenAI Agents SDK — `install_safer_for_agents`

```python
from agents import Runner
from safer.adapters.openai_agents import install_safer_for_agents

hooks = install_safer_for_agents(agent_id="repo_analyst",
                                  agent_name="Repo Analyst")
result = await Runner.run(agent, "task", hooks=hooks)
```

Registers a global `TracingProcessor` (idempotent) and returns a
`SaferRunHooks` instance for the run.  Native layer: the SDK's
first-class `RunHooks` + `TracingProcessor` surface — handoffs,
guardrails, and multi-agent runs all flow through.

#### OpenTelemetry bridge (opt-in alternative)

If you already run an OTel pipeline and prefer the OpenLLMetry
instrumentor over SAFER's native client wrappers:

```python
from safer.adapters.otel import configure_otel_bridge

configure_otel_bridge(agent_id="support", instrument=["anthropic"])
# or instrument=["openai"]
```

Needs `pip install 'safer-sdk[otel-anthropic]'` (or `[otel-openai]`).
Coverage is more limited than the native wrappers above (see the
hook-coverage table below) — the user-side tool execution isn't
OTel-instrumented, so `before/after_tool_use` only fires for tools the
underlying instrumentor sees.

#### Vanilla Python (no framework) — manual

```python
from safer import Hook, instrument, track_event

instrument(agent_id="my-agent", agent_name="My Agent")
track_event(Hook.ON_SESSION_START, {"agent_name": "my-agent"},
            session_id="sess_1", agent_id="my-agent")
# ...one track_event per lifecycle point.
```

See [`examples/vanilla-python`](examples/vanilla-python) for a
complete walkthrough.

---

## Framework matrix

The `Verified hook coverage` column reflects what each adapter actually
emits in the unit-test suite — not a marketing number.  An "auto" hook is
one that fires without the user adding manual `track_event` calls beyond
the two-line integration shown.

| Framework | Integration layer | Verified hook coverage | Two-line adapter call |
|---|---|---|---|
| Google ADK | Runner plugin (`BasePlugin`) | **10/10** auto (1 onboarding + 9 runtime, all unit-tested) | `plugins=[SaferAdkPlugin(agent_id=..., agent_name=...)]` |
| AWS Strands | Agent hooks (`HookProvider`) | **10/10** auto for single-agent runs; multi-agent / graph events not bridged yet | `hooks=[SaferHookProvider(agent_id=..., agent_name=...)]` |
| LangChain (AgentExecutor) | Callback handler | **10/10** auto via `on_chain_start/end` + `on_agent_action/finish` | `config={"callbacks": [SaferCallbackHandler(agent_id=...)]}` |
| LangChain LCEL / LangGraph | Callback handler | **10/10** auto — root run_id detection closes the SAFER session even without `on_agent_finish` | same as above |
| Anthropic (raw SDK) — native subclass | `Anthropic`/`AsyncAnthropic` subclass + `cached_property` override | **10/10** auto — incl. tool_use detection + tool_result pairing on next call | `client = SaferAnthropic(agent_id=..., api_key=...)` |
| Anthropic (raw SDK) — `wrap_anthropic` | `messages.create` instrumented | **10/10** auto for `messages.create` + `messages.stream`; `final_output` is manual | `wrap_anthropic(Anthropic(), agent_id=...)` |
| OpenAI (raw SDK) — `wrap_openai` | `chat.completions` + `responses` instrumented | **10/10** auto incl. async, streaming (chat + responses), `with_raw_response`, tool_call detection, `tool_result` pairing | `wrap_openai(OpenAI(), agent_id=...)` |
| OpenAI Agents SDK | `RunHooks` + `TracingProcessor` | **10/10** auto via the SDK's first-class hook surface — handoffs, multi-agent runs included | `Runner.run(agent, input, hooks=install_safer_for_agents(agent_id=...))` |
| Anthropic / OpenAI — OTel bridge (opt-in) | OpenLLMetry instrumentor → `/v1/traces` | **6-7/10**: session, before/after_llm, on_error, final_output, session_end auto; `on_agent_decision` synthesized when chat span carries `gen_ai.tool.call.id`; `before/after_tool_use` only when the user's tool code emits its own `execute_tool` spans (raw SDK calls don't) | `configure_otel_bridge(agent_id=..., instrument=["anthropic"])` |
| AWS Bedrock Agents | — | not yet implemented | use `safer.track_event(...)` until a native adapter ships |
| CrewAI | — | not yet implemented | same |
| LlamaIndex / AutoGen / anything | Custom SDK | manual | `safer.track_event(Hook.*, payload)` |

> **Tip.** For raw OpenAI / Anthropic code, prefer the native subclass
> (`SaferAnthropic`) or `wrap_*` helper over the OTel bridge — they
> see the model's tool_use intent and can pair tool results, while the
> OTel bridge can only observe what the SDK itself instruments.

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

### Hook coverage by adapter

| Adapter | session start/end | llm call pair | tool use pair | agent decision | final output | error |
|---|---|---|---|---|---|---|
| Google ADK `SaferAdkPlugin` | ✅ rotates per `Runner.run_async` invocation | ✅ | ✅ | ✅ (synth from `on_event` tool_use) | ✅ | ✅ |
| Strands `SaferHookProvider` | ✅ rotates per `agent(prompt)` invocation | ✅ | ✅ | ✅ (synth from `MessageAdded`) | ✅ | ✅ |
| LangChain `SaferCallbackHandler` | ✅ root run_id (works for AgentExecutor, LCEL, LangGraph) | ✅ | ✅ | ✅ | ✅ | ✅ |
| LangChain `AsyncSaferCallbackHandler` | ✅ same logic, native async dispatch | ✅ | ✅ | ✅ | ✅ | ✅ |
| Anthropic `SaferAnthropic` (native subclass) | ✅ | ✅ (incl. `messages.stream`) | ✅ tool_use auto-detect + `tool_result` pairing | ✅ from response content | ✅ via `final_output()` | ✅ |
| Anthropic `SaferAsyncAnthropic` | ✅ | ✅ async + stream | ✅ | ✅ | ✅ | ✅ |
| Anthropic `wrap_anthropic` proxy | manual (`agent.start_session`) | ✅ | ✅ tool_use auto-detect + pairing | ✅ | manual | ✅ |
| OpenAI `wrap_openai` (chat.completions) | ✅ via first call + `atexit` close | ✅ + streaming accumulator | ✅ tool_call detection + `role="tool"` pairing | ✅ | ✅ on stream/finish_reason | ✅ |
| OpenAI `wrap_openai` (responses) | ✅ | ✅ + `output_text` extraction | ✅ from `function_call` items | ✅ | ✅ | ✅ |
| OpenAI `wrap_openai` (`with_raw_response`) | ✅ | ✅ unwraps `LegacyAPIResponse.parse()` | ✅ | ✅ | ✅ | ✅ |
| OpenAI Agents SDK `SaferRunHooks` | ✅ rotates per `Runner.run` | ✅ from `ModelResponse.usage` | ✅ from `ToolContext` | ✅ (synth on tool_start + handoff) | ✅ from `on_agent_end` | ✅ via `SaferTracingProcessor` |
| OTel bridge (Anthropic / OpenAI) | ✅ per trace_id | ✅ | ⚠️ only when `execute_tool` spans exist | ⚠️ synth from `gen_ai.tool.call.id` when present | ✅ from root span end | ✅ |

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

| Feature | Model | Prompt cached | Why |
|---|---|---|---|
| Multi-Persona Judge | Opus 4.7 | ✅ `ephemeral` | Runtime critical-path; 6-persona role-play; cache bucket shared with Inspector |
| Inspector — 3-persona review | Opus 4.7 | ✅ | Same system prompt as the Judge — cache hits across both |
| Quality Reviewer | Sonnet 4.6 | ✅ | Session-end summary / goal-drift / hallucination — structured task, ~5× cheaper on Sonnet |
| Thought-Chain Reconstructor | Sonnet 4.6 | ✅ | Forensic narrative; Sonnet's strong suit |
| Policy Compiler | Sonnet 4.6 | ✅ | NL → deterministic rule_json + test cases |
| Red-Team Strategist | Opus 4.7 | ✅ | Creative attack planning; demo-critical |
| Red-Team Attacker | Opus 4.7 | ✅ | Adversarial creativity; demo-critical |
| Red-Team Analyst | Sonnet 4.6 | ✅ | Clustering + OWASP mapping — structured |
| Per-step Haiku score | Haiku 4.5 | — | Fast relevance + escalate signal at decision hooks only |
| Session Report aggregator | — | — | **Pure Python, zero Claude calls** |

Hard rules (also in [`CLAUDE.md`](CLAUDE.md)):

- **Claude-only.** No OpenAI / Gemini / Llama calls anywhere in the
  product code. Other frameworks plug in through adapters; the
  reasoning engine is always Claude.
- **Three models in rotation.** Opus 4.7 for runtime reasoning +
  cache-sharing siblings; Sonnet 4.6 for post-run / structured /
  clustering work; Haiku 4.5 for per-step decision-hook scoring.
  Per-call-site defaults live in CLAUDE.md's Model routing table and
  every call-site has an `SAFER_*_MODEL` env var override.
- **Every Opus / Sonnet call uses prompt caching** (`ephemeral` cache
  control). Cache hit rate > 80 % on the second call onward; verified
  by engine unit tests.
- **No `temperature` parameter on any call-site.** Anthropic deprecated
  it on modern Claude models (Opus 4.7 returns 400); deterministic
  behaviour comes from strict JSON schemas + repair passes. The
  Red-Team Attacker's earlier `temperature=0.8` is gone — adversarial
  variance now comes from the attack prompts themselves.

---

## Examples

| Path | Framework | Demo |
|---|---|---|
| [`examples/google-adk`](examples/google-adk) | Google ADK + Gemini | Repo Analyst agent via `Runner(plugins=[SaferAdkPlugin(...)])` — 10/10 SAFER hooks automatic |
| [`examples/strands`](examples/strands) | Strands Agents + Anthropic | System Diagnostic agent via `Agent(hooks=[SaferHookProvider(...)])` with real `ps` / `df` / log tools + a dangerous `run_shell` for policy demos |
| [`examples/anthropic-otel`](examples/anthropic-otel) | Raw Anthropic SDK + OTel bridge | Tool-calling loop observed via `configure_otel_bridge(instrument=["anthropic"])` — see hook coverage table for current OTel limits |
| [`examples/openai-otel`](examples/openai-otel) | Raw OpenAI SDK + OTel bridge | `summarize_url` tool loop observed via `configure_otel_bridge(instrument=["openai"])` |
| [`examples/customer-support`](examples/customer-support) | Anthropic Claude Agent SDK (low-level client proxy) | Customer-support bot with intentionally risky tools — demo for `wrap_anthropic` + manual helpers |
| [`examples/code-analyst`](examples/code-analyst) | LangChain + `langchain-anthropic` | Tool-calling agent that reads / greps / AST-scans this repo; shows `SaferCallbackHandler` end-to-end |
| [`examples/vanilla-python`](examples/vanilla-python) | None (custom SDK) | Minimal 60-line manual instrumentation using `safer.track_event()` |
| [`examples/coding_assistant`](examples/coding_assistant) | Anthropic Agent SDK supervisor + worker | Multi-agent chat with 6 tools and 4 deliberate security flaws — exercises `/live` parent/child sessions, Inspector findings, Gateway block path |

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
uv run pytest              # 357 tests, 2 skipped
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
│  │        ├─ _bootstrap.py    # shared ensure_runtime() helper
│  │        ├─ langchain.py     # ✅ Bundled (BaseCallbackHandler, 10/10)
│  │        ├─ google_adk.py    # ✅ Bundled (BasePlugin, 10/10)
│  │        ├─ strands.py       # ✅ Bundled (HookProvider, 10/10)
│  │        ├─ otel.py          # ✅ OTel bridge (Anthropic + OpenAI, 10/10)
│  │        ├─ claude_sdk.py    # 🔶 Client proxy (3/10 + manual helpers)
│  │        ├─ openai_agents.py # 🔶 Client proxy (4/10 + manual helpers)
│  │        ├─ bedrock.py       # 🔶 Stub (planned)
│  │        └─ crewai.py        # 🔶 Stub (planned)
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
