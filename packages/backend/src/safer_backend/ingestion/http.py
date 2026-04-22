"""HTTP event ingestion — fallback when WebSocket is unavailable.

Also hosts the OTLP /v1/traces endpoint (basic OTel GenAI span
→ event mapping; full implementation in later phases).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..router import route_event
from .normalizer import NormalizationError, normalize_event

log = logging.getLogger("safer.ingestion.http")

router = APIRouter()


class EventsPayload(BaseModel):
    events: list[dict[str, Any]]


@router.post("/v1/events")
async def post_events(payload: EventsPayload) -> dict[str, Any]:
    """Batch event ingest."""
    accepted = 0
    rejected: list[str] = []
    for raw in payload.events:
        try:
            event = normalize_event(raw)
        except NormalizationError as e:
            rejected.append(str(e))
            continue
        await route_event(event)
        accepted += 1
    return {"accepted": accepted, "rejected": rejected}


@router.post("/v1/traces")
async def post_otlp_traces(payload: dict[str, Any]) -> dict[str, Any]:
    """OTLP GenAI span ingest — basic shim for OTel-emitting frameworks.

    Full OTLP parsing (resource, scope, span attributes per GenAI semconv)
    will land when we formalize the OTel adapter. For now we accept a
    pre-normalized `events` array if present, otherwise return 202 noop.
    """
    events = payload.get("events")
    if not events:
        return {"accepted": 0, "note": "OTLP full parser not yet implemented"}
    accepted = 0
    for raw in events:
        try:
            event = normalize_event(raw)
        except NormalizationError:
            continue
        await route_event(event)
        accepted += 1
    return {"accepted": accepted}
