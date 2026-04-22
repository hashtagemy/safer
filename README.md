# SAFER — Agent Control Plane

> **Open-source agent control plane for the entire AI agent lifecycle.**
> Onboarding → Pre-deploy → Runtime → Post-run.
> Self-hosted. Framework-agnostic. Powered by Claude.

**Status:** 🚧 Work in progress — Claude Hackathon 2026 (submission: 2026-04-26)

---

## What it does

SAFER hooks into any AI agent framework with **one line of code** (`instrument()`) and gives you:

- **Inspector** — scan the agent's code before deploy with 3 personas (Security Auditor, Compliance Officer, Policy Warden) in a single Opus 4.7 call. Get a risk score + auto-suggested policies before it even runs.
- **Multi-Persona Judge** — at runtime, each critical event is judged by the right subset of 6 personas (dynamic routing: Policy Warden + Scope Enforcer always live, Ethics Reviewer only on final output, etc.) in a single Opus call.
- **Gateway** — pre-call enforcement with deterministic PII regex + natural-language policies (write rules in English, Claude compiles them).
- **Red-Team Squad** — manually-triggered security testing via 3 Claude Managed Agents (Strategist → Attacker → Analyst), mapped to OWASP LLM Top 10.
- **Session Report** — per-session health card: 7 category scores + top findings + Thought-Chain narrative + OWASP map. Deterministic aggregation, no extra Claude calls.
- **Compliance Pack** — export GDPR / SOC 2 / OWASP LLM Top 10 reports as PDF / HTML / JSON.

---

## Framework matrix

| Framework | Support | How |
|---|---|---|
| Anthropic Claude Agent SDK | ✅ Bundled | `wrap_anthropic(client, agent_id=...)` — all 9 hooks automatic |
| LangChain / LangGraph | ✅ Bundled | `SaferCallbackHandler` — pass via `callbacks=[...]` (9 hooks) |
| OpenAI Agents SDK | 🔶 Partial | `wrap_openai(client, agent_id=...)` — `before/after_llm_call` automatic; tool / decision hooks via `safer.track_event()` for now |
| Google ADK | 🔶 Stub | `wrap_adk(...)` no-op wrapper + warning; manual `safer.track_event()` today |
| AWS Bedrock Agents | 🔶 Stub | `wrap_bedrock(...)` no-op wrapper + warning; manual `safer.track_event()` today |
| CrewAI | 🔶 Stub | `wrap_crew(...)` no-op wrapper + warning; manual `safer.track_event()` today |
| AWS Strands | 🟡 OTel | Native OTel, works through the OTLP ingestion shim |
| LlamaIndex / AutoGen / anything else | 🔵 Custom SDK | 10 lines via `safer.track_event(Hook.*, payload)` — see `examples/vanilla-python` |

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/hashtagemy/safer.git
cd safer

# 2. Copy env
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY

# 3. Run
docker compose up
# → Dashboard at http://localhost:5173
# → Backend at http://localhost:8000
```

Then in your agent code:

```python
from safer import instrument
instrument()
# That's it. Events stream to the dashboard.
```

---

## Examples

| Path | Framework | Demo |
|---|---|---|
| [`examples/customer-support`](examples/customer-support) | Anthropic Agent SDK | Customer-support bot with intentional PII-risk tools |
| [`examples/code-analyst`](examples/code-analyst) | LangChain + `langchain-anthropic` | Tool-calling agent that reads / greps / AST-scans this repo |
| [`examples/vanilla-python`](examples/vanilla-python) | None (custom SDK) | 60-line manual instrumentation using `safer.track_event()` |

Each example has its own `README.md` with run instructions.

---

## How to add your framework

If your framework isn't on the matrix, you have two options:

1. **Emit events manually** — call `safer.track_event(Hook.X, payload)`
   at each lifecycle point in your run loop. See
   [`examples/vanilla-python/main.py`](examples/vanilla-python/main.py).
2. **Write a real adapter** — add
   `packages/sdk/src/safer/adapters/<framework>.py`, subclass /
   proxy your framework's native callback surface, and translate
   events into the 9 SAFER hooks. The LangChain adapter
   ([`safer/adapters/langchain.py`](packages/sdk/src/safer/adapters/langchain.py))
   is a small, complete example — ~350 lines, one file. PRs
   welcome.

---

## Dev workflow

```bash
# Backend + SDK
uv sync
uv run pytest

# Dashboard
cd packages/dashboard
bun install
bun run dev
```

---

## Architecture

High-level: SDK adapter catches framework-native hooks → normalizes to 9 lifecycle events → backend router applies dynamic persona selection → Multi-Persona Judge (Opus 4.7) evaluates in a single call → Gateway enforces policies pre-call → Session Report aggregates deterministically at session end.

Full architecture docs will be published in `docs/` during development.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Compliance

SAFER uses the Anthropic API under standard commercial terms. All API calls use user-provided credentials. Data remains within the user's self-hosted deployment. Red-Team features are user-authorized defensive security testing only, consistent with Anthropic's Usage Policy permissions for security research.
