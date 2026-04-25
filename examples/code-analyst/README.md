# code-analyst (LangChain demo) — **hero example**

A "Code Analyst" chat agent built on LangChain + `langchain-anthropic`
that explores files inside the SAFER repo. Instrumented via
`SaferCallbackHandler`, so every LLM call, tool call, and agent
decision flows into the SAFER dashboard with zero extra work.

This is one of the two **recommended starting points** for a live
walkthrough — it opens an interactive REPL by default so you can chat
with it while watching `/live` light up.

## What it does

Seven repo-aware tools, all read-only:

| Tool | What it does |
|---|---|
| `read_file(path)` | First 2 KB of a text file (boundary-checked) |
| `search_codebase(query)` | `grep -rn` under `packages/`, up to 20 hits |
| `analyze_ast(path)` | Imports / classes / top-level functions of a Python file |
| `list_directory(path)` | One-level directory listing |
| `count_lines(path)` | total / blank / code line counts |
| `find_definitions(symbol)` | `def` / `class` definition sites for a symbol |
| `git_log_for_path(path, limit=5)` | Recent commits touching a path |

The agent's system prompt nudges it to plan a small investigation
(explore → drill in → check history → synthesise) so a single
substantive question typically fires 3–6 tool calls.

## Run it

```bash
pip install safer-sdk[langchain,claude] langchain-anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Backend + dashboard:
docker compose up

# Interactive chat (default):
uv run python examples/code-analyst/main.py

# One-shot (good for CI / smoke tests):
uv run python examples/code-analyst/main.py --prompt "..."
```

Inside the REPL: `clear` wipes conversation memory; `quit` / `:q` /
Ctrl-D exits.

### Prompts to try

- `Explain what the safer-sdk package does — start from packages/sdk.`
- `Where is SaferBlocked raised? Show me one site and explain it.`
- `Has packages/backend/src/safer_backend/main.py changed recently?`
- `Compare the LangChain adapter with the Strands adapter — what's similar?`

## What gets emitted

| Hook | When |
|---|---|
| `on_session_start` | LangChain chain kicks off |
| `before_llm_call` / `after_llm_call` | Each LLM call (chat model) |
| `before_tool_use` / `after_tool_use` | Each of the seven tools |
| `on_agent_decision` | Tool-calling agent picks a tool |
| `on_final_output` + `on_session_end` | Agent finishes |
| `on_error` | Any callback raises |

Open `http://localhost:5173/live` while the REPL runs to watch events
arrive. Then `/sessions/<id>` for the full timeline + persona verdicts.

## SAFER integration — the two lines

```python
from safer import instrument
from safer.adapters.langchain import SaferCallbackHandler

instrument(agent_id="code_analyst", agent_name="Code Analyst")
handler = SaferCallbackHandler(agent_id="code_analyst", agent_name="Code Analyst")

executor.invoke({"input": prompt}, config={"callbacks": [handler]})
```
