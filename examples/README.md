# Examples

Every example here is a runnable SAFER integration on a different
framework. Each directory has its own `README.md` with the full
install/env/run recipe.

| Directory | Framework / layer | Integration call |
|---|---|---|
| [`google-adk/`](google-adk) | Google ADK `BasePlugin` | `Runner(plugins=[SaferAdkPlugin(...)])` |
| [`strands/`](strands) | Strands `HookProvider` | `Agent(hooks=[SaferHookProvider(...)])` |
| [`anthropic-otel/`](anthropic-otel) | Anthropic SDK + OpenTelemetry bridge | `configure_otel_bridge(instrument=["anthropic"])` |
| [`openai-otel/`](openai-otel) | OpenAI SDK + OpenTelemetry bridge | `configure_otel_bridge(instrument=["openai"])` |
| [`code-analyst/`](code-analyst) | LangChain callback handler | `SaferCallbackHandler(...)` + `config={"callbacks":[...]}` |
| [`customer-support/`](customer-support) | Anthropic SDK (low-level client proxy) | `wrap_anthropic(client, ...)` + manual helpers |
| [`coding_assistant/`](coding_assistant) | Claude Agent SDK (multi-agent chat) | `wrap_anthropic(client, ...)` |
| [`vanilla-python/`](vanilla-python) | None (manual) | `safer.track_event(Hook.*, payload)` |

Prefer the framework-native adapters (Google ADK / Strands /
LangChain) or the OTel bridge for zero-config full-hook coverage.
The `customer-support` and `coding_assistant` examples showcase the
client-proxy + manual-helper pattern for the raw Anthropic SDK; for a
new project on raw Anthropic or OpenAI, the OTel bridge is the
recommended path.
