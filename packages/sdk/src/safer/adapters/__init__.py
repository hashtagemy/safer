"""Framework adapters.

Bundled (automatic instrumentation):
    - `claude_sdk.wrap_anthropic`    — Anthropic Agent SDK
    - `langchain.SaferCallbackHandler` — LangChain callbacks (9 hooks)

Partial (LLM-call pair bridged, other hooks via `safer.track_event`):
    - `openai_agents.wrap_openai`

Beta stubs (import-safe no-op wrappers; emit a warning once):
    - `google_adk.wrap_adk`
    - `bedrock.wrap_bedrock`
    - `crewai.wrap_crew`

All helpers are imported lazily so third-party deps stay optional.
"""

from __future__ import annotations


def _lazy_claude():
    from . import claude_sdk as _m

    return _m


def _lazy_langchain():
    from . import langchain as _m

    return _m


def _lazy_openai_agents():
    from . import openai_agents as _m

    return _m


__all__ = ["_lazy_claude", "_lazy_langchain", "_lazy_openai_agents"]
