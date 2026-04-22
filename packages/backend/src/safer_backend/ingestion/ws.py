"""WebSocket /ingest — SDK event stream endpoint."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..router import route_event
from .normalizer import NormalizationError, normalize_event

log = logging.getLogger("safer.ingestion.ws")

router = APIRouter()


@router.websocket("/ingest")
async def ingest_ws(ws: WebSocket) -> None:
    """Accept ndjson-over-WebSocket event stream from SDK.

    Sends `{"accepted": N, "rejected": M}` JSON ack after each batch so
    clients (and tests) can synchronize on processing completion.
    """
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            accepted = 0
            rejected = 0
            # SDK may send a batch as newline-delimited JSON lines.
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    log.warning("invalid JSON line from SDK: %s", e)
                    rejected += 1
                    continue
                try:
                    event = normalize_event(data)
                except NormalizationError as e:
                    log.warning("normalization failed: %s", e)
                    rejected += 1
                    continue
                await route_event(event)
                accepted += 1
            await ws.send_json({"accepted": accepted, "rejected": rejected})
    except WebSocketDisconnect:
        return
    except Exception as e:  # pragma: no cover
        log.exception("ws ingest error: %s", e)
