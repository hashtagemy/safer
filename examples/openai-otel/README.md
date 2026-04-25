# OpenAI + SAFER via OpenTelemetry bridge — Research Assistant

Raw OpenAI Python SDK code instrumented with SAFER — no `wrap_openai`,
no manual helpers. Every `chat.completions.create` becomes a GenAI
span via `opentelemetry-instrumentation-openai`, shipped to SAFER's
`/v1/traces`, and parsed into the 9-hook model.

This example is the OpenAI sibling of `anthropic-otel/`: a small
research assistant with the same six tools.

## What it does

Six tools:

| Tool | What it does |
|---|---|
| `web_search(query, max_results=5)` | Wikipedia opensearch (no API key required) |
| `fetch_url(url)` | Real `httpx.get`, first 2 KB |
| `extract_links(url)` | Up to 20 outbound `<a href>` from a fetched page |
| `save_note(title, body)` | Persists a markdown note to `.notes/<slug>.md` |
| `read_note(title)` | Reads a previously saved note |
| `list_notes()` | Lists every saved note's title |

Notes survive across REPL turns *and* across runs — `.notes/` is
git-ignored.

## Install

```bash
pip install 'safer-sdk[otel-openai]'
export OPENAI_API_KEY=...
export SAFER_API_URL=http://localhost:8000   # optional default
```

## Run

```bash
# Interactive chat (default):
python examples/openai-otel/main.py

# One-shot:
python examples/openai-otel/main.py --prompt "..."
```

REPL: `clear` wipes the chat history; `quit` / `:q` / Ctrl-D exits.

### Prompts to try

- `Look up "Retrieval augmented generation" and write me a 5-line summary as a note.`
- `Fetch https://example.com and tell me what's there.`
- `What notes do we have right now?`

## SAFER integration — the two lines

```python
from safer.adapters.otel import configure_otel_bridge

configure_otel_bridge(
    agent_id="openai_otel_demo",
    agent_name="OpenAI OTel Research Assistant",
    instrument=["openai"],
)
```

## What you'll see in the SAFER dashboard

- **`/agents`** — `openai_otel_demo` card.
- **`/live`** — GenAI spans from `chat.completions.create` expand into
  SAFER's 9 hooks (session start/end, LLM call pair, tool call pair,
  agent decision, final output, error). Watch them arrive turn by
  turn as you chat.
- **`/sessions/<id>`** — trace tree + persona verdicts.

The cost column uses OpenAI's public pricing as a signal — the
authoritative number is in your OpenAI billing dashboard.
