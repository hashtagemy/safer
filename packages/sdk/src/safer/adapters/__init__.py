"""Framework adapters.

Bundled:
    - claude_sdk (Phase 4)
    - langchain (Phase 15)

Partial / stub (Phase 15):
    - openai_agents (before/after LLM only)
    - google_adk, bedrock, crewai (import stubs)
"""

from __future__ import annotations


def _lazy_claude():
    from . import claude_sdk as _m

    return _m


__all__ = ["_lazy_claude"]
