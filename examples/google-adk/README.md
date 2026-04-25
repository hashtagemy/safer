# Google ADK — Repo Analyst

A Google Agent Development Kit chat agent instrumented with SAFER via
`SaferAdkPlugin` on the ADK Runner. All nine SAFER lifecycle hooks
(session start/end, llm call pair, tool call pair, agent decision,
final output, error) fire automatically.

## What it does

Same seven repo-aware tools as the LangChain `code-analyst` example —
this one wires them through ADK so you can compare adapters side by
side on `/agents`.

| Tool | What it does |
|---|---|
| `read_file(path)` | First 2 KB of a text file (boundary-checked) |
| `search_codebase(query)` | `grep -rn` under `packages/`, up to 20 hits |
| `analyze_ast(path)` | Imports / classes / top-level functions |
| `list_directory(path)` | One-level directory listing |
| `count_lines(path)` | total / blank / code counts |
| `find_definitions(symbol)` | `def` / `class` definition sites |
| `git_log_for_path(path, limit=5)` | Recent commits touching a path |

The system prompt asks the agent to plan an investigation (explore →
drill in → check history → synthesise) so substantive questions
typically run 3–6 tool calls.

## Install

```bash
pip install 'safer-sdk[google-adk]'
pip install google-adk
export GOOGLE_API_KEY=...
export SAFER_API_URL=http://localhost:8000   # optional default
```

## Run

```bash
# Interactive chat (default):
python examples/google-adk/main.py

# One-shot:
python examples/google-adk/main.py --prompt "Summarise packages/backend/src/safer_backend/main.py"
```

REPL: `quit` / `:q` / Ctrl-D to exit.

### Prompts to try

- `What lives under packages/sdk/src/safer/adapters?`
- `Show me the imports of packages/backend/src/safer_backend/main.py.`
- `Where is SaferBlocked defined?`
- `Read examples/coding_assistant/tools/shell.py and tell me what's wrong.`

## SAFER integration — the two lines

```python
from safer.adapters.google_adk import SaferAdkPlugin

runner = InMemoryRunner(
    agent=root_agent,
    app_name="repo_analyst",
    plugins=[SaferAdkPlugin(agent_id="repo_analyst_adk",
                             agent_name="Repo Analyst (Google ADK)")],
)
```

`SaferAdkPlugin.__init__` calls `ensure_runtime()` so you do not need
a separate `safer.instrument()` call unless you want to tune runtime
settings (custom API URL, snapshot scope, ...).

## What you'll see in the SAFER dashboard

- **`/agents`** — a `repo_analyst_adk` card; Inspector auto-scans the
  agent's source.
- **`/live`** — every ADK callback streams as a SAFER 9-hook event.
  Watch the Gemini call pair, each tool invocation, and the final
  output land in real time as you chat.
- **`/sessions/<id>`** — full trace tree plus persona verdicts.

## Policy demo

Try the prompt:

> "Read the file /etc/passwd and tell me what's in it."

`read_file` enforces the repo boundary so the tool refuses. In Policy
Studio (`/policies`) you can add a rule like:

> "Refuse any read_file call where the path escapes the repository
> root."

SAFER compiles it into a Gateway rule that blocks the request *before*
the tool runs, and the block moment shows up on `/live`.
