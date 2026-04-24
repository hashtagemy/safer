"""HTTP event ingestion.

`POST /v1/events` — batch ingest for pre-normalized SAFER events (used
by adapters that can't hold a WebSocket, or as a fallback transport).

`POST /v1/traces` — OTLP GenAI span ingest. Accepts either
`application/x-protobuf` or `application/json` `ExportTraceServiceRequest`
payloads sent by any OpenTelemetry exporter (see
`safer.adapters.otel.configure_otel_bridge`). Spans are parsed against
GenAI semantic conventions and fan out as SAFER 9-hook events — see
`ingestion/otlp.py`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..router import route_event
from .normalizer import NormalizationError, normalize_event
from .otlp import map_genai_span_to_safer, parse_otlp_request

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
async def post_otlp_traces(request: Request) -> dict[str, Any]:
    """OTLP GenAI span ingest.

    Accepts binary protobuf (`application/x-protobuf`, default OTel
    exporter format) or JSON (`application/json`) bodies. Decoded spans
    are mapped into SAFER 9-hook events via GenAI semantic conventions
    and routed through the standard pipeline.
    """
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty request body")
    content_type = request.headers.get("content-type", "application/x-protobuf")
    try:
        spans = parse_otlp_request(body, content_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    accepted_spans = 0
    emitted_events = 0
    for span in spans:
        mapped = map_genai_span_to_safer(span)
        if not mapped:
            continue
        accepted_spans += 1
        for ev in mapped:
            await route_event(ev)
            emitted_events += 1
    return {
        "accepted_spans": accepted_spans,
        "emitted_events": emitted_events,
    }
