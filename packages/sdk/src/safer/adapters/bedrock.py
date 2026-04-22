"""AWS Bedrock adapter — BETA / stub.

Placeholder module so `from safer.adapters.bedrock import wrap_bedrock`
imports cleanly. Emits a one-time warning and returns the client
unchanged; bridge events manually via `safer.track_event()`.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("safer.adapters.bedrock")

_WARNED = False


def _warn_once() -> None:
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    log.warning(
        "safer.adapters.bedrock is a stub. Emit lifecycle events via "
        "`safer.track_event(...)` until automatic bridging lands."
    )


def wrap_bedrock(client: Any, *, agent_id: str, **_ignored: Any) -> Any:
    """No-op wrapper. Returns the same Bedrock client, logs a warning once."""
    _warn_once()
    return client


__all__ = ["wrap_bedrock"]
