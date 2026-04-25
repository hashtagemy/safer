# Anthropic raw SDK + SAFER hook (chat REPL)

The shortest path to a real Claude chat agent on the SAFER dashboard:
`SaferAnthropic` is a drop-in replacement for `anthropic.Anthropic`
that emits SAFER lifecycle events for every `messages.create()` call.

| Layer | Choice |
|---|---|
| Framework | Raw `anthropic` SDK (you write the loop) |
| Model | `claude-haiku-4-5` by default (cheap + fast) |
| SAFER integration | `SaferAnthropic(...)` — already chat-friendly |
| Tools | none |
| Anthropic API key | **required** (`ANTHROPIC_API_KEY` in `.env`) |

> The raw SDK adapter is already pinned to one session per client +
> registers an `atexit` hook that fires `on_session_end` once on exit.
> No `pin_session` flag needed.

## Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...
SAFER_API_URL=http://127.0.0.1:8000 \
SAFER_WS_URL=ws://127.0.0.1:8000/ingest \
  uv run python examples/anthropic-chat/main.py
```

Switch model with `--model claude-sonnet-4-6` or `--model claude-opus-4-7`.
