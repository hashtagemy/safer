"""Shared helper for adapter constructors.

Every SAFER adapter (`wrap_anthropic`, `wrap_openai`, `SaferCallbackHandler`,
`SaferAdkPlugin`, `SaferHookProvider`) calls `ensure_runtime()` as its
very first step. If the user has not yet called `instrument()`, this
starts the SAFER runtime transparently so the two-line integration
pattern works:

    from safer.adapters.strands import SaferHookProvider
    agent = Agent(..., hooks=[SaferHookProvider(agent_id="x", agent_name="X")])

`instrument()` itself is idempotent — calling it twice returns the
existing client — so a user who wants to customize runtime settings
(custom `api_url`, `scan_mode`, etc.) can still call `instrument(...)`
explicitly before constructing the adapter; the adapter's call will
short-circuit.
"""

from __future__ import annotations

from ..client import get_client


def ensure_runtime(
    agent_id: str,
    agent_name: str | None = None,
    *,
    framework: str | None = None,
) -> None:
    """Start the SAFER runtime if it isn't running yet.

    No-op when `get_client()` already returns a live client. The import
    of `instrument` is deferred so that unit tests can install a dummy
    client via `monkeypatch.setattr(client_mod, "_client", ...)` without
    pulling in the instrument module.

    `framework` is the adapter's self-declared framework label (e.g.
    `"google-adk"`, `"openai-agents"`). When set it overrides runtime
    module detection so an environment with multiple frameworks
    installed still labels each agent correctly.
    """
    if get_client() is not None:
        return
    from ..instrument import instrument

    instrument(
        agent_id=agent_id,
        agent_name=agent_name,
        framework_hint=framework,
    )


__all__ = ["ensure_runtime"]
