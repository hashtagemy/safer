"""Google ADK adapter — BETA / stub.

No automatic instrumentation yet. The module exists so
`from safer.adapters.google_adk import wrap_adk` doesn't raise an
ImportError; it emits a one-time warning and returns the original
client unchanged. Bridge events manually via `safer.track_event()`.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("safer.adapters.google_adk")

_WARNED = False


def _warn_once() -> None:
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    log.warning(
        "safer.adapters.google_adk is a stub. For now, call "
        "`safer.track_event(Hook.BEFORE_LLM_CALL, ...)` around your ADK "
        "run loop manually. Automatic bridging is on the roadmap."
    )


def wrap_adk(client: Any, *, agent_id: str, **_ignored: Any) -> Any:
    """No-op wrapper. Returns the same ADK client, logs a warning once."""
    _warn_once()
    return client


__all__ = ["wrap_adk"]
