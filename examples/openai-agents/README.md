# OpenAI Agents SDK — Two-Agent Triage Demo

An `openai-agents` setup with three agents (`triage`, `math_specialist`,
`geo_specialist`) wired through SAFER via
`install_safer_for_agents(...)`.  The triage agent hands off to a
specialist based on the user's question; each specialist has its own
tool.  SAFER captures the full lifecycle — handoffs included — without
any manual hook calls beyond the install line.

## What it exercises

| SAFER hook | Source in the demo |
|---|---|
| `on_session_start` | First `on_agent_start` from `Runner.run` |
| `before_llm_call` / `after_llm_call` | Each LLM turn (triage + specialist), with real `usage` / cost from `ModelResponse.usage` |
| `before_tool_use` / `after_tool_use` | `add`, `country_capital` calls with parsed arguments |
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
python examples/openai-agents/main.py
python examples/openai-agents/main.py --prompt "What's 17 + 25 and capital of France?"
```

Then open the SAFER dashboard at <http://localhost:5173> and watch the
session land in `/live`, `/sessions`, and `/agents`.

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

`install_safer_for_agents` is idempotent — calling it multiple times for
the same `agent_id` registers the global `TracingProcessor` only once and
returns a fresh `SaferRunHooks` instance per call.
