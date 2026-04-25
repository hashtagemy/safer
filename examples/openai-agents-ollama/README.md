# OpenAI Agents SDK + Ollama + SAFER hook (chat REPL)

OpenAI's `agents` framework (Agent / Runner / handoffs / tool-calling)
pointed at Ollama's OpenAI-compatible endpoint. SAFER instrumentation
via `install_safer_for_agents(pin_session=True)`.

| Layer | Choice |
|---|---|
| Framework | OpenAI Agents SDK (`agents`) |
| Model | Gemma 4 via Ollama (OpenAI-compatible) |
| SAFER integration | `install_safer_for_agents(..., pin_session=True)` |
| Tools | `add_numbers`, `get_current_weather` |
| API keys | **not required** |

## Prerequisites

```bash
ollama serve &
ollama pull gemma4:31b
uv pip install openai-agents
```

## Run

```bash
SAFER_API_URL=http://127.0.0.1:8000 \
SAFER_WS_URL=ws://127.0.0.1:8000/ingest \
  uv run python examples/openai-agents-ollama/main.py
```

Each chat turn lands in the same SAFER session — open
[/sessions](http://127.0.0.1:5174/sessions) and you'll see one row,
not one per message.
