"""Dashboard broadcaster — fans out ingested events to /ws/stream subscribers."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("safer.broadcaster")


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._subscribers.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._subscribers.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        if not self._subscribers:
            return
        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        async with self._lock:
            subs = list(self._subscribers)
        for ws in subs:
            try:
                await ws.send_text(payload)
            except Exception as e:  # pragma: no cover — network hiccup
                log.debug("broadcast drop: %s", e)
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._subscribers.discard(ws)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Module-level singleton used by both ingestion and the /ws/stream route.
broadcaster = Broadcaster()
