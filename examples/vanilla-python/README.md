# vanilla-python

A short demo of how to instrument an agent framework that SAFER does
not have a bundled adapter for. The trick: call
`safer.track_event(Hook.*, payload)` at each of the lifecycle points
your agent goes through. No framework, no proxy, no callbacks.

The example is a tiny "capital lookup" agent: it pretends to call an
LLM, pretends to call a `lookup_city` tool, and emits the seven
SAFER events you would emit in a real implementation.

## Run

```bash
# Backend running (`docker compose up`).

# Interactive: each turn fires the full lifecycle event sequence.
uv run python examples/vanilla-python/main.py

# One-shot:
uv run python examples/vanilla-python/main.py --prompt "France"
```

REPL: `quit` / `:q` / Ctrl-D to exit.

## What gets emitted per turn

```
on_session_start
before_llm_call → after_llm_call
before_tool_use → after_tool_use     (or SaferBlocked → on_session_end if blocked)
on_final_output
on_session_end
```

`on_agent_register` fires once per process via the
`safer.instrument(...)` call at startup — that's the 8th event you
will see in `/live` for the first turn.

## Why this is the "no adapter" path

Replace `fake_llm_response` and `fake_lookup_city` with the real
client your agent uses (any LLM SDK, any tool runner). Wrap each
side-effect with the matching `track_event(Hook.*, payload)` call.
You get the same 9-hook contract as the bundled adapters — just by
hand.

For frameworks SAFER already supports natively (Anthropic / OpenAI /
LangChain / Google ADK / Strands / OpenAI Agents SDK), prefer the
adapter — it does the hook plumbing for you.
