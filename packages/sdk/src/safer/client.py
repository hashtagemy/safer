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
        self._thread: threading.Thread | None = None
        self._started = False

    # ---------- public ----------

    def start(self) -> None:
        """Idempotent. Always spawns a dedicated background event-loop thread.

        We deliberately do NOT piggyback on a caller's already-running
        asyncio loop (e.g. the one `asyncio.run(...)` opens for an ADK
        agent): when that outer loop closes, atexit fires while our
        transport's loop reference is dead and we cannot drain
        `on_session_end` to the backend. A dedicated thread keeps the
        loop alive long enough for `client.stop()` (registered as an
        atexit handler) to flush the queue. Thread is daemon so a stray
        client never holds the interpreter open past atexit.
        """
        if self._started:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="safer-loop", daemon=True
        )
        self._thread.start()
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
        """Graceful flush. Atexit-safe.

        First runs a synchronous, stdlib-only drain so the queue is
        emptied even if interpreter shutdown is mid-flight (when httpx
        cannot register its own atexit hooks). Then asks the async
        worker to stop on a best-effort basis — it may or may not get
        the message depending on how far along the daemon thread is.
        """
        if not self._started or self._loop is None:
            return
        # 1. Sync drain — runs no third-party code that might call
        #    atexit.register(), so it survives interpreter shutdown.
        try:
            self.transport.sync_drain()
        except Exception as e:  # pragma: no cover
            log.warning("SAFER sync drain error: %s", e)
        # 2. Best-effort async stop. If the loop is already torn down
        #    (e.g. asyncio.run() exited), this fails silently.
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.transport.stop(), self._loop
            )
            future.result(timeout=self.config.shutdown_timeout_s + 1.0)
        except Exception as e:  # pragma: no cover
            log.debug("SAFER async stop skipped: %s", e)
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

    def schedule_profile_patch(
        self,
        agent_id: str,
        *,
        system_prompt: str | None = None,
        name: str | None = None,
        version: str | None = None,
    ) -> None:
        """Fire-and-forget PATCH /v1/agents/{id}/profile via transport loop.

        Adapters call this the first time they see a system prompt so the
        Agents dashboard learns the prompt without a manual instrument()
        keyword argument.
        """
        if not self._started or self._loop is None:
            return
        coro = self.transport.patch_agent_profile(
            agent_id,
            system_prompt=system_prompt,
            name=name,
            version=version,
        )
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as e:  # pragma: no cover — defensive
            log.debug("SAFER: could not schedule profile patch: %s", e)

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
