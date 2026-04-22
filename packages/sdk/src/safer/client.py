"""Global SaferClient — module-level singleton managed by instrument()."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any
from uuid import uuid4

from .config import SaferConfig
from .events import EventBase, Hook, parse_event
from .transport import AsyncTransport

log = logging.getLogger("safer")


class SaferClient:
    """Module-level client. One per process.

    Usage is normally indirect via `instrument()` / `track_event()`.
    """

    def __init__(self, config: SaferConfig) -> None:
        self.config = config
        self.transport = AsyncTransport(config)
        self._sequence: dict[str, int] = {}  # session_id → counter
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False

    # ---------- public ----------

    def start(self) -> None:
        """Idempotent. Spawns a background event loop thread if none exists."""
        if self._started:
            return
        try:
            self._loop = asyncio.get_running_loop()
            # Running loop exists → schedule on it.
            self._loop.create_task(self.transport.start())
        except RuntimeError:
            # No running loop → create one in a background thread.
            self._loop = asyncio.new_event_loop()
            t = threading.Thread(
                target=self._run_loop, name="safer-loop", daemon=True
            )
            t.start()
        self._started = True
        log.info("SAFER started (guard_mode=%s, url=%s)", self.config.guard_mode, self.config.api_url)

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self.transport.start())
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def stop(self) -> None:
        """Graceful flush. Blocks up to shutdown_timeout_s."""
        if not self._started or self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self.transport.stop(), self._loop)
        try:
            future.result(timeout=self.config.shutdown_timeout_s + 1.0)
        except Exception as e:  # pragma: no cover
            log.warning("SAFER shutdown error: %s", e)
        self._started = False

    def emit(self, event: EventBase | dict[str, Any]) -> None:
        """Public emit. Accepts a Pydantic event or raw dict."""
        if isinstance(event, EventBase):
            raw = event.model_dump(mode="json")
        else:
            raw = dict(event)
        self.transport.emit(raw)

    def next_sequence(self, session_id: str) -> int:
        with self._lock:
            n = self._sequence.get(session_id, 0)
            self._sequence[session_id] = n + 1
        return n

    def new_session_id(self) -> str:
        return f"sess_{uuid4().hex[:12]}"

    def track_event(
        self,
        hook: Hook | str,
        payload: dict[str, Any],
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Manual API for Custom SDK users (vanilla Python agents).

        Required fields in payload are inferred per hook type.
        """
        if isinstance(hook, str):
            hook = Hook(hook)
        sid = session_id or payload.get("session_id") or self.new_session_id()
        aid = agent_id or payload.get("agent_id") or self.config.agent_id or "agent_unknown"

        full = {
            "session_id": sid,
            "agent_id": aid,
            "sequence": self.next_sequence(sid),
            "hook": hook.value,
            "source": payload.get("source", "manual"),
            **payload,
        }
        # Parse through Pydantic for validation.
        event = parse_event(full)
        self.emit(event)


# Module-level singleton holder.
_client: SaferClient | None = None
_client_lock = threading.Lock()


def get_client() -> SaferClient | None:
    return _client


def set_client(client: SaferClient) -> None:
    global _client
    with _client_lock:
        _client = client


def clear_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.stop()
        _client = None
