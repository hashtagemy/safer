"""SAFER SDK — instrument any AI agent framework with one line.

Basic usage:

    from safer import instrument
    instrument()

Or manually for vanilla Python agents:

    from safer import track_event, Hook
    track_event(Hook.BEFORE_LLM_CALL, payload={
        "model": "claude-opus-4-7",
        "prompt": prompt,
    })
"""

from __future__ import annotations

from typing import Any

from .client import SaferClient, get_client
from .events import (
    AfterLLMCallPayload,
    AfterToolUsePayload,
    BeforeLLMCallPayload,
    BeforeToolUsePayload,
    Event,
    Hook,
    OnAgentDecisionPayload,
    OnAgentRegisterPayload,
    OnErrorPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnSessionStartPayload,
    RiskHint,
)
from .exceptions import SaferBlocked, SaferError
from .instrument import instrument

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "instrument",
    "track_event",
    "Hook",
    "RiskHint",
    "Event",
    "SaferClient",
    "SaferError",
    "SaferBlocked",
    "get_client",
    # Event payload classes (useful for type hints in user code)
    "OnAgentRegisterPayload",
    "OnSessionStartPayload",
    "BeforeLLMCallPayload",
    "AfterLLMCallPayload",
    "BeforeToolUsePayload",
    "AfterToolUsePayload",
    "OnAgentDecisionPayload",
    "OnFinalOutputPayload",
    "OnSessionEndPayload",
    "OnErrorPayload",
]


def track_event(
    hook: Hook | str,
    payload: dict[str, Any],
    session_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Emit a lifecycle event manually. Use this if no bundled adapter fits.

    `hook` must be one of the Hook enum values.
    `payload` should include hook-specific fields (see `safer.events`).
    If `session_id` / `agent_id` are omitted, sensible defaults are used.
    """
    client = get_client()
    if client is None:
        # Auto-init — users can call track_event without instrument().
        client = instrument()
    client.track_event(hook, payload, session_id=session_id, agent_id=agent_id)
