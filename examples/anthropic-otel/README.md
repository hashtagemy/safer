# Anthropic + SAFER via OpenTelemetry bridge

Raw Anthropic Python SDK code instrumented with SAFER — no
`wrap_anthropic` call, no manual hook helpers. Every `messages.create`
becomes a GenAI span, shipped to SAFER's `/v1/traces`, and parsed
into the 9-hook event model on the backend.

## What it does

A small tool-calling loop: the assistant asks for a file's first line
via the `read_tech_news` tool, we execute the tool in Python, feed the
result back, and let Claude write the final answer.

## Install

```bash
pip install 'safer-sdk[otel-anthropic]'
export ANTHROPIC_API_KEY=...
export SAFER_API_URL=http://localhost:8000   # optional
```

`safer-sdk[otel-anthropic]` pulls in the OpenTelemetry SDK, the OTLP
HTTP exporter, and `opentelemetry-instrumentation-anthropic`.

## Run

```bash
python examples/anthropic-otel/main.py
python examples/anthropic-otel/main.py --prompt "custom question"
```

## SAFER integration — the two lines

```python
from safer.adapters.otel import configure_otel_bridge

configure_otel_bridge(
    agent_id="anthropic_otel_demo",
    agent_name="Anthropic OTel Demo",
    instrument=["anthropic"],
)
```

`configure_otel_bridge`:
1. Calls `ensure_runtime()` so SAFER's SDK side boots (snapshot +
   onboarding event).
2. Installs a `TracerProvider` pointing at
   `$SAFER_API_URL/v1/traces`.
3. Enables `AnthropicInstrumentor` which monkey-patches
   `Anthropic.messages.create` to emit GenAI spans.

Every subsequent Anthropic call in this process is observed —
including tool use blocks.

## What you'll see in the SAFER dashboard

- **`/agents`** — a new `anthropic_otel_demo` card.
- **`/live`** — GenAI spans fan out into:
  - `on_session_start` on the first span of the trace.
  - `before_llm_call` / `after_llm_call` per `messages.create`.
  - `before_tool_use` / `after_tool_use` per tool span.
  - `on_final_output` + `on_session_end` on root span close.
- **`/sessions/<id>`** — the full trace tree and persona verdicts.
