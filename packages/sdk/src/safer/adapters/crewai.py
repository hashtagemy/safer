"""CrewAI adapter — BETA / stub.

Placeholder module so `from safer.adapters.crewai import wrap_crew`
imports cleanly. Emits a one-time warning and returns the crew object
unchanged; bridge events manually via `safer.track_event()`.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("safer.adapters.crewai")

_WARNED = False


def _warn_once() -> None:
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    log.warning(
        "safer.adapters.crewai is a stub. Emit lifecycle events via "
        "`safer.track_event(...)` until automatic bridging lands."
    )


def wrap_crew(crew: Any, *, agent_id: str, **_ignored: Any) -> Any:
    """No-op wrapper. Returns the same Crew object, logs a warning once."""
    _warn_once()
    return crew


__all__ = ["wrap_crew"]
