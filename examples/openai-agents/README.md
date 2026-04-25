# OpenAI Agents SDK — Multi-Specialist Triage Chat

An `openai-agents` setup with four agents (`triage`,
`math_specialist`, `geo_specialist`, `weather_specialist`) wired
through SAFER via `install_safer_for_agents(...)`. The triage agent
hands off to a specialist based on the user's question; each
specialist has its own tools. SAFER captures the full lifecycle —
handoffs included — without any manual hook calls beyond the install
line.

## What it does

| Specialist | Tools |
|---|---|
| `math_specialist` | `add(a, b)`, `multiply(a, b)`, `sqrt(x)` |
| `geo_specialist` | `country_capital(c)`, `country_population(c)`, `distance_km(a, b)` |
| `weather_specialist` | `current_weather(city)`, `forecast(city, days)` (real wttr.in HTTP) |

Triage hands off based on the user's question. Multi-question prompts
chain handoffs.

## What it exercises

| SAFER hook | Source in the demo |
|---|---|
| `on_session_start` | First `on_agent_start` from `Runner.run` |
| `before_llm_call` / `after_llm_call` | Each LLM turn (triage + specialist), with `usage` / cost from `ModelResponse.usage` |
| `before_tool_use` / `after_tool_use` | Every tool call with parsed arguments |
| `on_agent_decision` (`tool_call`) | Synthesised on every tool start |
| `on_agent_decision` (`handoff`) | Triage → specialist transition (`on_handoff`) |
| `on_final_output` | Final agent's `on_agent_end` |
| `on_session_end` | Same — closes the session |

## Install

```bash
pip install openai-agents safer-sdk
export OPENAI_API_KEY=...
export SAFER_API_URL=http://localhost:8000   # optional default
```

## Run

```bash
# Interactive chat (default):
python examples/openai-agents/main.py

# One-shot:
python examples/openai-agents/main.py --prompt "What's 17 + 25 and the capital of France?"
```

REPL: `quit` / `:q` / Ctrl-D to exit.

### Prompts to try

- `What is sqrt(2025) and what's the capital of Japan?` *(math + geo)*
- `Distance between Paris and Tokyo, and how many people live in Brazil?` *(geo)*
- `What's the weather in Ankara right now and the 3-day forecast?` *(weather)*
- `How big is Germany's population, and what's the weather in Berlin?` *(geo + weather)*

## SAFER integration — the install line

```python
from agents import Agent, Runner, function_tool
from safer.adapters.openai_agents import install_safer_for_agents

hooks = install_safer_for_agents(
    agent_id="agents_sdk_demo",
    agent_name="OpenAI Agents Demo",
)
result = await Runner.run(triage_agent, prompt, hooks=hooks)
```

`install_safer_for_agents` is idempotent — calling it multiple times
for the same `agent_id` registers the global `TracingProcessor` only
once and returns a fresh `SaferRunHooks` instance per call.

Open the SAFER dashboard at <http://localhost:5173> and watch the
session land in `/live`, `/sessions`, and `/agents`.
