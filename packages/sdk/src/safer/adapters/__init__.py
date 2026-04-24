"""Framework adapters.

Bundled (native hook bridges across all 9 SAFER hooks):
    - `langchain.SaferCallbackHandler` — LangChain `BaseCallbackHandler`
    - `google_adk.SaferAdkPlugin`      — Google ADK `BasePlugin`
    - `strands.SaferHookProvider`      — Strands `HookProvider`

Client proxies (LLM-call pair bridged; tool / agent-decision via
manual helpers — prefer the OTel bridge for zero-config full coverage):
    - `claude_sdk.wrap_anthropic`
    - `openai_agents.wrap_openai`

Beta stubs (import-safe no-op wrappers, planned):
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


def _lazy_google_adk():
    from . import google_adk as _m

    return _m


def _lazy_strands():
    from . import strands as _m

    return _m


__all__ = [
    "_lazy_claude",
    "_lazy_google_adk",
    "_lazy_langchain",
    "_lazy_openai_agents",
    "_lazy_strands",
]
