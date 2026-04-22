"""The `instrument()` one-liner.

Detects installed frameworks (Claude Agent SDK, LangChain, ...) and
registers the appropriate adapter. Users who use vanilla Python can
still emit events via `safer.track_event()`.
"""

from __future__ import annotations

import atexit
import logging
from typing import Any

from .client import SaferClient, clear_client, get_client, set_client
from .config import SaferConfig

log = logging.getLogger("safer")


def instrument(
    *,
    api_url: str | None = None,
    api_key: str | None = None,
    guard_mode: str | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    **extra_config: Any,
) -> SaferClient:
    """Initialize SAFER. Idempotent — subsequent calls return the same client.

    Auto-detects installed frameworks:
        - anthropic Agent SDK → claude_sdk adapter
        - langchain → langchain adapter

    Framework not detected? Use `safer.track_event(...)` manually.
    """
    existing = get_client()
    if existing is not None:
        return existing

    overrides: dict[str, Any] = {}
    if api_url is not None:
        overrides["api_url"] = api_url
    if api_key is not None:
        overrides["api_key"] = api_key
    if guard_mode is not None:
        overrides["guard_mode"] = guard_mode
    if agent_id is not None:
        overrides["agent_id"] = agent_id
    if agent_name is not None:
        overrides["agent_name"] = agent_name
    overrides.update(extra_config)

    config = SaferConfig.from_env(**overrides)
    client = SaferClient(config)
    client.start()
    set_client(client)

    # Graceful shutdown at process exit.
    atexit.register(clear_client)

    # Register framework adapters (best-effort, non-fatal).
    _register_adapters(client)

    return client


def _register_adapters(client: SaferClient) -> None:
    """Detect installed frameworks and register matching adapters.

    Full adapters land in later phases. This scaffolding logs detected
    frameworks so users know what's active.
    """
    detected: list[str] = []

    try:
        import anthropic  # noqa: F401

        detected.append("anthropic")
    except ImportError:
        pass

    try:
        import langchain  # noqa: F401

        detected.append("langchain")
    except ImportError:
        pass

    try:
        import openai  # noqa: F401

        detected.append("openai")
    except ImportError:
        pass

    if detected:
        log.info("SAFER: detected frameworks: %s", ", ".join(detected))
    else:
        log.info(
            "SAFER: no framework detected; use safer.track_event() for manual instrumentation"
        )
