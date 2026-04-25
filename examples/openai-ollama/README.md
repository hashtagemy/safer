# OpenAI raw SDK + Ollama + SAFER hook (chat REPL)

Ollama exposes an OpenAI-compatible REST endpoint, so we can point the
official `openai` SDK at `http://127.0.0.1:11434/v1` and chat with
Gemma 4 locally — zero OpenAI / Anthropic key needed.

| Layer | Choice |
|---|---|
| Framework | Raw `openai` SDK |
| Model | Gemma 4 via Ollama (OpenAI-compatible) |
| SAFER integration | `wrap_openai(OpenAI(...))` — already chat-friendly |
| Tools | none |
| API keys | **not required** (Ollama ignores the key value) |

> The raw SDK adapter is already pinned to one session per client +
> registers an `atexit` hook that fires `on_session_end` once on exit.
> No `pin_session` flag needed.

## Prerequisites

```bash
ollama serve &
ollama pull gemma4:31b
```

## Run

```bash
SAFER_API_URL=http://127.0.0.1:8000 \
SAFER_WS_URL=ws://127.0.0.1:8000/ingest \
  uv run python examples/openai-ollama/main.py
```
