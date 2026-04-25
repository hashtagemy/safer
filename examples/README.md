# Examples

Every example here is a runnable SAFER integration on a different
framework. Each one opens an **interactive chat REPL by default** so
you can watch the SAFER dashboard's `/live` view light up while you
talk to the agent. Pass `--prompt "..."` to any of them for a single
one-shot run instead.

> **Try this first:** [`code-analyst/`](code-analyst) (LangChain) and
> [`coding_assistant/`](coding_assistant) (multi-agent) are the two
> recommended hero examples for a live walkthrough.

## Examples

| Directory | Framework / layer | Tools | Integration call |
|---|---|---|---|
| [`code-analyst/`](code-analyst) ★ | LangChain callback handler | 7 (repo analysis + git) | `SaferCallbackHandler(...)` + `config={"callbacks":[...]}` |
| [`google-adk/`](google-adk) | Google ADK `BasePlugin` | 7 (same as code-analyst) | `Runner(plugins=[SaferAdkPlugin(...)])` |
| [`strands/`](strands) | Strands `HookProvider` | 8 (system diagnostic) | `Agent(hooks=[SaferHookProvider(...)])` |
| [`anthropic-otel/`](anthropic-otel) | Anthropic SDK + OTel bridge | 6 (research assistant) | `configure_otel_bridge(instrument=["anthropic"])` |
| [`openai-otel/`](openai-otel) | OpenAI SDK + OTel bridge | 6 (research assistant) | `configure_otel_bridge(instrument=["openai"])` |
| [`openai-agents/`](openai-agents) | OpenAI Agents SDK (RunHooks + tracing) | 8 (3 specialists + triage) | `install_safer_for_agents(...)` + `Runner.run(..., hooks=hooks)` |
| [`customer-support/`](customer-support) | Anthropic SDK (low-level client proxy) | 6 (orders + refunds + email) | `wrap_anthropic(client, ...)` + manual helpers |
| [`coding_assistant/`](coding_assistant) ★ | Claude Agent SDK (multi-agent chat) | 9 worker tools (fs / web / shell / git) | `wrap_anthropic(client, ...)` |
| [`vanilla-python/`](vanilla-python) | None (manual) | n/a | `safer.track_event(Hook.*, payload)` |

★ = recommended hero examples for a first walkthrough.

Prefer the framework-native adapters (Google ADK / Strands /
LangChain / OpenAI Agents) or the OTel bridge for zero-config full
hook coverage. The `customer-support` and `coding_assistant` examples
showcase the client-proxy + manual-helper pattern for the raw
Anthropic SDK; for a new project on raw Anthropic or OpenAI, the OTel
bridge is the recommended path.

## Running any example

```bash
# 1. Backend + dashboard:
docker compose up

# 2. Pick an example and run it (chat REPL by default):
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY / GOOGLE_API_KEY
uv run python examples/code-analyst/main.py

# Or one-shot:
uv run python examples/code-analyst/main.py --prompt "..."
```

Inside any REPL: `quit` / `exit` / `:q` / Ctrl-D exits; `clear`
(where supported) resets the conversation memory.

The dashboard lives at <http://localhost:5173>. The agent cards land
on `/agents`; the live event stream on `/live`; the per-session
timeline on `/sessions/<id>`.
