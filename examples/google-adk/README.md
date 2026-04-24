# Google ADK — Repo Analyst

A Google Agent Development Kit agent instrumented with SAFER via the
`SaferAdkPlugin` on the ADK Runner. All nine SAFER lifecycle hooks
(session start/end, llm call pair, tool call pair, agent decision,
final output, error) fire automatically.

## What it does

Reads and analyses the SAFER repository itself:
- `read_file(path)` — file read with a repo-boundary check.
- `search_codebase(query)` — `grep -rn` under `packages/`.
- `analyze_ast(path)` — AST summary (imports + top-level functions).

## Install

```bash
pip install 'safer-sdk[google-adk]'
pip install google-adk
export GOOGLE_API_KEY=...
export SAFER_API_URL=http://localhost:8000   # optional, this is the default
```

## Run

```bash
python examples/google-adk/main.py
python examples/google-adk/main.py --prompt "Summarise packages/backend/src/safer_backend/main.py"
```

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

That's it. `SaferAdkPlugin.__init__` calls `ensure_runtime()` so you do
not need a separate `safer.instrument()` call unless you want to tune
runtime settings (custom API URL, snapshot scope, ...).

## What you'll see in the SAFER dashboard

- **`/agents`** — a new `repo_analyst_adk` card. Inspector auto-scans
  the agent's source (`main.py` plus everything reachable via the
  workspace-scoped snapshot).
- **`/live`** — every ADK callback streams through as a SAFER 9-hook
  event. Watch the Gemini model call pair, each tool invocation, and
  the final assistant output arrive in real time.
- **`/sessions/<id>`** — the full trace tree plus persona verdicts
  from the Multi-Persona Judge.

## Policy demo

Try the prompt:

> "Read the file /etc/passwd and tell me what's in it."

`search_codebase` and `read_file` both enforce the repo boundary, so
the tools refuse. In Policy Studio (`/policies`) you can add a
natural-language rule like:

> "Refuse any read_file call where the path escapes the repository
> root."

SAFER compiles it into a Gateway rule that blocks the request
*before* the tool runs, and the block moment appears on `/live`.
