# OpenAI + SAFER via OpenTelemetry bridge

Raw OpenAI Python SDK code instrumented with SAFER — no
`wrap_openai`, no manual helpers. Every `chat.completions.create`
becomes a GenAI span via `opentelemetry-instrumentation-openai`, gets
shipped to SAFER's `/v1/traces`, and parsed into the 9-hook model.

## What it does

Tool-calling loop: the assistant requests a summary of a URL via
`summarize_url`; we execute a real `httpx.get` and hand the snippet
back.

## Install

```bash
pip install 'safer-sdk[otel-openai]'
export OPENAI_API_KEY=...
export SAFER_API_URL=http://localhost:8000   # optional
```

## Run

```bash
python examples/openai-otel/main.py
python examples/openai-otel/main.py --prompt "custom question"
```

## SAFER integration — the two lines

```python
from safer.adapters.otel import configure_otel_bridge

configure_otel_bridge(
    agent_id="openai_otel_demo",
    agent_name="OpenAI OTel Demo",
    instrument=["openai"],
)
```

## What you'll see in the SAFER dashboard

- **`/agents`** — a new `openai_otel_demo` card.
- **`/live`** — GenAI spans from `chat.completions.create` expand into
  SAFER's 9 hooks (session start/end, LLM call pair, tool call pair,
  agent decision, final output, error).
- **`/sessions/<id>`** — trace tree + persona verdicts.

The cost column uses OpenAI's public pricing as a signal — the
authoritative number is in your OpenAI billing dashboard.
