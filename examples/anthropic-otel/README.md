# Anthropic + SAFER via OpenTelemetry bridge — Research Assistant

Raw Anthropic Python SDK code instrumented with SAFER — no
`wrap_anthropic` call, no manual hook helpers. Every `messages.create`
becomes a GenAI span, shipped to SAFER's `/v1/traces`, and parsed
into the 9-hook event model on the backend.

This example is a small **research assistant** that searches the web,
reads pages, and saves notes that survive across REPL turns.

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
git-ignored. The agent is encouraged to drill into multiple sources
and capture findings before answering.

## Install

```bash
pip install 'safer-sdk[otel-anthropic]'
export ANTHROPIC_API_KEY=...
export SAFER_API_URL=http://localhost:8000   # optional default
```

`safer-sdk[otel-anthropic]` pulls in the OpenTelemetry SDK, the OTLP
HTTP exporter, and `opentelemetry-instrumentation-anthropic`.

## Run

```bash
# Interactive chat (default):
python examples/anthropic-otel/main.py

# One-shot:
python examples/anthropic-otel/main.py --prompt "Research Anthropic Claude and save the highlights as a note."
```

REPL: `clear` wipes the chat history; `quit` / `:q` / Ctrl-D exits.
Saved notes are kept in `examples/anthropic-otel/.notes/`.

### Prompts to try

- `Research the SAFER project on Wikipedia and save what you find.`
- `Read https://example.com and tell me what it actually serves.`
- `What were the notes you took in the last session?` *(after restarting)*
- `Search for "OpenTelemetry GenAI semantic conventions" and summarise the top result.`

## SAFER integration — the two lines

```python
from safer.adapters.otel import configure_otel_bridge

configure_otel_bridge(
    agent_id="anthropic_otel_demo",
    agent_name="Anthropic OTel Research Assistant",
    instrument=["anthropic"],
)
```

`configure_otel_bridge`:
1. Calls `ensure_runtime()` so SAFER's SDK side boots (snapshot +
   onboarding event).
2. Installs a `TracerProvider` pointing at `$SAFER_API_URL/v1/traces`.
3. Enables `AnthropicInstrumentor`, which monkey-patches
   `Anthropic.messages.create` to emit GenAI spans.

Every Anthropic call in this process is observed — including tool use
blocks.

## What you'll see in the SAFER dashboard

- **`/agents`** — `anthropic_otel_demo` card.
- **`/live`** — GenAI spans fan out into `before/after_llm_call`,
  `before/after_tool_use`, `on_agent_decision`, and the session
  start/end pair. Watch them arrive turn by turn as you chat.
- **`/sessions/<id>`** — full trace tree and persona verdicts.
