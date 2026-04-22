"""Raw payload → validated Pydantic event."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from safer.events import Event, parse_event


class NormalizationError(Exception):
    """Raised when a raw event fails validation."""


def normalize_event(raw: dict[str, Any]) -> Event:
    """Parse a raw dict into one of the 9 Pydantic event payloads.

    Raises NormalizationError on bad shape; callers should log + drop.
    """
    try:
        return parse_event(raw)
    except (KeyError, ValueError, ValidationError) as e:
        raise NormalizationError(f"invalid event: {e}") from e
