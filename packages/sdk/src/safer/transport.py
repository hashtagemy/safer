"""SDK transport — batched async event emission.

Primary: WebSocket (ndjson-over-WS). Fallback: HTTP POST to /v1/events.
Batching, backpressure, graceful shutdown, credential masking inline.

Backpressure policy: if the internal queue exceeds `max_buffer`, events
are dropped with a warning. SAFER never blocks the host agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]

    class ConnectionClosed(Exception):  # type: ignore[no-redef]
        pass


from .config import SaferConfig
from .masking import mask_payload

log = logging.getLogger("safer.transport")


class AsyncTransport:
    """Async batched transport with WS primary + HTTP fallback."""

    def __init__(self, config: SaferConfig) -> None:
        self.config = config
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._ws: Any | None = None
        self._http: httpx.AsyncClient | None = None
        self._shutdown = asyncio.Event()
        self._dropped = 0

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self._worker is not None:
            return
        self._http = httpx.AsyncClient(timeout=5.0)
        self._worker = asyncio.create_task(self._run(), name="safer-transport")

    async def stop(self) -> None:
        """Graceful shutdown — flush remaining batch within shutdown_timeout_s."""
        self._shutdown.set()
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._worker, timeout=self.config.shutdown_timeout_s)
            except asyncio.TimeoutError:
                self._worker.cancel()
                try:
                    await self._worker
                except (asyncio.CancelledError, Exception):
                    pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # pragma: no cover
                pass
        if self._http is not None:
            await self._http.aclose()

    # ---------- emit ----------

    def emit(self, event: dict[str, Any]) -> None:
        """Non-blocking. Masks credentials, enqueues. Drops with warning on overflow."""
        if self._queue.qsize() >= self.config.max_buffer:
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning(
                    "SAFER: transport buffer full (%d), dropping event (total dropped: %d)",
                    self.config.max_buffer,
                    self._dropped,
                )
            return
        masked = mask_payload(event)
        try:
            self._queue.put_nowait(masked)
        except asyncio.QueueFull:  # pragma: no cover — unbounded queue
            self._dropped += 1

    @property
    def dropped_count(self) -> int:
        return self._dropped

    # ---------- internal ----------

    async def _run(self) -> None:
        """Worker loop: collect up to batch_size or batch_interval_ms, then flush."""
        while not self._shutdown.is_set():
            batch = await self._collect_batch()
            if batch:
                await self._flush(batch)
        # drain remaining on shutdown
        final_batch: list[dict[str, Any]] = []
        while not self._queue.empty():
            final_batch.append(self._queue.get_nowait())
            if len(final_batch) >= self.config.batch_size:
                await self._flush(final_batch)
                final_batch = []
        if final_batch:
            await self._flush(final_batch)

    async def _collect_batch(self) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        deadline = asyncio.get_event_loop().time() + (self.config.batch_interval_ms / 1000.0)
        while len(batch) < self.config.batch_size:
            timeout = max(0.0, deadline - asyncio.get_event_loop().time())
            if timeout == 0.0 and batch:
                break
            try:
                evt = await asyncio.wait_for(self._queue.get(), timeout=timeout or 0.001)
                batch.append(evt)
            except asyncio.TimeoutError:
                break
        return batch

    async def _flush(self, batch: list[dict[str, Any]]) -> None:
        # Try WebSocket first
        if await self._try_ws(batch):
            return
        # Fallback: HTTP
        await self._try_http(batch)

    async def _try_ws(self, batch: list[dict[str, Any]]) -> bool:
        if websockets is None:
            return False
        try:
            if self._ws is None:
                ws_url = self.config.api_url.replace("http://", "ws://").replace(
                    "https://", "wss://"
                )
                ws_url = f"{ws_url.rstrip('/')}/ingest"
                self._ws = await websockets.connect(ws_url)  # type: ignore[union-attr]
            ndjson = "\n".join(json.dumps(e) for e in batch)
            await self._ws.send(ndjson)  # type: ignore[union-attr]
            return True
        except (ConnectionClosed, OSError, Exception) as e:
            log.debug("WS send failed, falling back to HTTP: %s", e)
            try:
                if self._ws is not None:
                    await self._ws.close()
            except Exception:  # pragma: no cover
                pass
            self._ws = None
            return False

    async def _try_http(self, batch: list[dict[str, Any]]) -> None:
        if self._http is None:
            return
        url = f"{self.config.api_url.rstrip('/')}/v1/events"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        try:
            await self._http.post(url, json={"events": batch}, headers=headers)
        except Exception as e:  # pragma: no cover — network issues
            log.warning("SAFER: HTTP fallback failed: %s (dropping batch of %d)", e, len(batch))

    # ---------- REST side-channel (fire-and-forget) ----------

    async def patch_agent_profile(
        self,
        agent_id: str,
        *,
        system_prompt: str | None = None,
        name: str | None = None,
        version: str | None = None,
    ) -> None:
        """Best-effort PATCH /v1/agents/{id}/profile. Never raises."""
        if self._http is None:
            return
        body: dict[str, Any] = {}
        if system_prompt is not None:
            body["system_prompt"] = mask_payload({"v": system_prompt}).get("v")
        if name is not None:
            body["name"] = name
        if version is not None:
            body["version"] = version
        if not body:
            return
        url = f"{self.config.api_url.rstrip('/')}/v1/agents/{agent_id}/profile"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        try:
            await self._http.patch(url, json=body, headers=headers)
        except Exception as e:  # pragma: no cover — network issues
            log.debug("SAFER: profile patch failed for %s: %s", agent_id, e)
