"""Policy Studio — compile natural-language policies into Gateway rules.

Public entry points:
- `compile_policy(nl_text)` — NL → `CompiledPolicy` (Opus 4.7, temperature=0,
   cached system prompt).
- FastAPI router `router` — mounted at `/v1/policies`.
"""

from __future__ import annotations

from .compiler import compile_policy, set_client

__all__ = ["compile_policy", "set_client"]
