# code-analyst (LangChain demo)

A small agent built on LangChain + `langchain-anthropic` that analyses
files inside the SAFER repo. Instrumented via `SaferCallbackHandler`,
so every LLM call, tool call, and agent decision flows into the SAFER
dashboard with zero extra work.

## Run it

```bash
pip install safer-sdk[langchain,claude] langchain-anthropic
export ANTHROPIC_API_KEY=sk-ant-...

uv run uvicorn safer_backend.main:app --reload &
(cd packages/dashboard && npm run dev) &

uv run python examples/code-analyst/main.py
uv run python examples/code-analyst/main.py --prompt "Search for 'sql_injection' in the codebase."
uv run python examples/code-analyst/main.py --prompt "Read packages/backend/src/safer_backend/main.py and list the routers."
```

## What gets emitted

| Hook | When |
|---|---|
| `on_session_start` | LangChain chain kicks off |
| `before_llm_call` / `after_llm_call` | Each LLM call (chat model) |
| `before_tool_use` / `after_tool_use` | Each of the three tools |
| `on_agent_decision` | Tool-calling agent picks a tool |
| `on_final_output` + `on_session_end` | Agent finishes |
| `on_error` | Any callback raises |

Open `http://localhost:5173/sessions` to open the session report + timeline.
