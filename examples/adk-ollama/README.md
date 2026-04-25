# Google ADK + Ollama (Gemma 4) + SAFER hook (chat REPL)

ADK is built around Gemini, but its `LiteLlm` model adapter brings
local Ollama in by way of LiteLLM's `ollama_chat/...` provider prefix.

| Layer | Choice |
|---|---|
| Framework | Google ADK `LlmAgent` + `InMemoryRunner` |
| Model | Gemma 4 via LiteLLM → Ollama |
| SAFER integration | `SaferAdkPlugin(pin_session=True)` |
| Tools | `add_numbers`, `get_current_weather` |
| Anthropic API key | **not required** |

## Prerequisites

```bash
ollama serve &
ollama pull gemma4:31b

uv pip install 'google-adk' litellm
```

## Run

```bash
SAFER_API_URL=http://127.0.0.1:8000 \
SAFER_WS_URL=ws://127.0.0.1:8000/ingest \
  uv run python examples/adk-ollama/main.py
```
