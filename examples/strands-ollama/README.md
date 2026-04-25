# Strands + Ollama (Gemma 4) + SAFER hook

The smallest end-to-end example to verify SAFER's 9-hook contract on a
fully-local Strands agent.

| Layer | Choice |
|---|---|
| Framework | Strands Agents (`strands`) |
| Model | Gemma 4 via Ollama (`gemma4:31b` by default) |
| SAFER integration | `SaferHookProvider` on the Strands `Agent` |
| Tools | `add_numbers`, `get_current_weather` (canned, offline) |
| Anthropic API key | **not required** — the agent runs locally |

## Prerequisites

```bash
# Ollama running locally with Gemma 4 pulled
ollama serve &
ollama pull gemma4:31b      # 19 GB; smaller Gemma variants work too

# Python dep used by Strands' Ollama provider
uv pip install ollama
```

A SAFER backend at `http://127.0.0.1:8000` is what the SDK ships
events to. Start it with `uv run uvicorn safer_backend.main:app --port 8000`
or via `docker compose up`.

## Run

```bash
uv run python examples/strands-ollama/main.py
```

Optional flags:

```bash
uv run python examples/strands-ollama/main.py \
    --prompt "Add 17 and 25 and tell me the current weather in Istanbul." \
    --model gemma4:31b \
    --host http://127.0.0.1:11434
```

Then open the dashboard at <http://127.0.0.1:5174/live> and watch the
event stream as the agent thinks, calls each tool, and finalises.

## What lands on the dashboard

`SaferHookProvider` translates Strands' native callbacks into SAFER's
9-hook contract automatically. For a single run with two tools you
should see (at minimum):

- `on_agent_register` — once, when `instrument()` is bootstrapped.
- `on_session_start` — when `agent(prompt)` is invoked.
- `before_llm_call` / `after_llm_call` — at least one round-trip.
- `on_agent_decision` — when Gemma picks `add_numbers`.
- `before_tool_use` / `after_tool_use` — for each tool call.
- `on_final_output` — the agent's final reply.
- `on_session_end` — closes the session.
- `on_error` — only if anything goes wrong (e.g., Ollama unreachable).

The Live page colour-codes each hook; the Sessions page shows the same
flow as a trace tree.
