"""SDK exceptions."""

from __future__ import annotations

from typing import Any


class SaferError(Exception):
    """Base exception for SAFER SDK errors."""


class SaferBlocked(SaferError):
    """Raised when the backend Judge returns a CRITICAL verdict with block=true,
    OR when the Gateway pre-call rejects an action (mode=enforce / intervene+CRITICAL).

    Host agent code should catch this and surface a user-facing reason from
    `verdict["overall"]` / `message`.

    Attributes
    ----------
    verdict: dict
        Structured verdict snapshot (persona scores, flags, reasoning).
    event_id: str
        The event that triggered the block.
    message: str
        User-facing reason, safe to show end-users.
    """

    def __init__(
        self,
        verdict: dict[str, Any],
        event_id: str,
        message: str = "Action blocked by SAFER",
    ) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.event_id = event_id
        self.message = message

    def __repr__(self) -> str:
        return f"SaferBlocked(event_id={self.event_id!r}, message={self.message!r})"
