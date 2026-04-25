# LangChain + Ollama (Gemma 4) + SAFER hook (chat REPL)

The smallest LangChain agent we can build that lands on the SAFER
dashboard, with `pin_session=True` so the whole conversation is one
SAFER session.

| Layer | Choice |
|---|---|
| Framework | LangChain `create_agent` (modern 1.x) |
| Model | Gemma 4 via Ollama (`langchain-ollama` `ChatOllama`) |
| SAFER integration | `SaferCallbackHandler(pin_session=True)` |
| Tools | `add_numbers`, `get_current_weather` (canned, offline) |
| Anthropic API key | **not required** |

## Prerequisites

```bash
ollama serve &
ollama pull gemma4:31b

uv pip install langchain-ollama
```

## Run

```bash
SAFER_API_URL=http://127.0.0.1:8000 \
SAFER_WS_URL=ws://127.0.0.1:8000/ingest \
  uv run python examples/langchain-ollama/main.py
```

Watch [http://127.0.0.1:5174/live](http://127.0.0.1:5174/live) and
[http://127.0.0.1:5174/sessions](http://127.0.0.1:5174/sessions): the
chat will appear as a **single** session with multiple turns inside.
