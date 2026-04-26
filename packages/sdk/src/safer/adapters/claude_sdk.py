"""Anthropic Claude adapter — native subclass + tool-use detection.

Two integration paths, both production-ready and async-aware:

**Path 1 — Native subclass (recommended for new code).**

    from safer.adapters.claude_sdk import SaferAnthropic, SaferAsyncAnthropic

    client = SaferAnthropic(agent_id="support", agent_name="Support Bot",
                            api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(model="claude-opus-4-7", ...)
    # → before_llm_call + after_llm_call emitted automatically.
    # → If response contains tool_use blocks, on_agent_decision +
    #   before_tool_use are auto-emitted.
    # → If the next messages.create includes tool_result blocks, those
    #   are auto-paired into after_tool_use events.

`SaferAnthropic` extends `anthropic.Anthropic` and overrides the
`messages` cached_property to return a `_SaferMessages` resource subclass
— this is the official Stainless extension point and is robust to SDK
regenerations.  `SaferAsyncAnthropic(AsyncAnthropic)` is the async twin.

**Path 2 — `wrap_anthropic` (backward compatible).**

    from anthropic import Anthropic
    from safer.adapters.claude_sdk import wrap_anthropic

    client = Anthropic()
    agent = wrap_anthropic(client, agent_id="support")
    agent.start_session()
    agent.messages.create(...)
    agent.before_tool_use("get_order", {"id": 123}); ...
    agent.final_output("..."); agent.end_session()

The wrapper returns an `AnthropicTracker` whose `messages.create` emits
before/after_llm_call automatically; tool_use blocks in the response
also auto-emit `on_agent_decision` + `before_tool_use`.  The remaining
hooks (`final_output`, `end_session`) stay manual because the raw SDK
has no notion of "session done".  `AsyncAnthropic` is fully supported —
the wrapper detects the client type and installs an async messages proxy.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from functools import cached_property
from typing import Any

from ..client import SaferClient, get_client
from ..events import (
    AfterLLMCallPayload,
    AfterToolUsePayload,
    BeforeLLMCallPayload,
    BeforeToolUsePayload,
    OnAgentDecisionPayload,
    OnErrorPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnSessionStartPayload,
)

log = logging.getLogger("safer.adapters.claude")


def _estimate_cost_usd(
    model: str,
    tokens_in: int,
    tokens_out: int,
    cache_read: int,
    cache_write: int,
) -> float:
    """Estimate USD cost via the shared pricing table; 0.0 for unknown models."""
    from .._pricing import estimate_cost

    cost = estimate_cost(
        model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        cache_write=cache_write,
    )
    return cost or 0.0


# ---------- shared event emission (used by tracker + native subclass) -------


class _AnthropicEventEmitter:
    """Common state + emission logic for both the proxy and the subclass.

    Holds session bookkeeping (`session_id`, `_step_count`, `_pending_tool_calls`,
    cumulative cost) and the helper methods that translate Anthropic SDK
    request/response objects into SAFER event payloads."""

    def __init__(
        self,
        *,
        agent_id: str,
        agent_name: str | None,
        session_id: str | None,
        safer_client: SaferClient | None,
    ) -> None:
        from ._bootstrap import ensure_runtime

        ensure_runtime(agent_id, agent_name, framework="anthropic")
        self.agent_id = agent_id
        self.agent_name = agent_name or agent_id
        self._initial_session_id = session_id
        self._current_session_id: str | None = None
        self._safer = safer_client
        self._step_count = 0
        self._session_started = False
        self._session_start_ts: float | None = None
        self._total_cost_usd = 0.0
        self._profile_synced = False
        # tool_use_id -> (tool_name, args, started_ts)
        # populated on tool_use response, drained when the next request's
        # messages array contains a matching tool_result block.
        self._pending_tool_calls: dict[str, tuple[str, dict[str, Any], float]] = {}

    @property
    def session_id(self) -> str:
        if self._current_session_id is None:
            self._current_session_id = (
                self._initial_session_id or f"sess_{uuid.uuid4().hex[:16]}"
            )
        return self._current_session_id

    def _next_seq(self) -> int:
        client = self._safer or get_client()
        if client is not None:
            try:
                return client.next_sequence(self.session_id)
            except Exception:
                pass
        n = self._step_count
        self._step_count += 1
        return n

    def _emit(self, event: Any) -> None:
        client = self._safer or get_client()
        if client is None:
            log.debug("SAFER not initialized; dropping %s", type(event).__name__)
            return
        try:
            client.emit(event)
        except Exception as e:  # pragma: no cover — transport errors
            log.debug("SAFER emit failed: %s", e)

    # ---------- session lifecycle ----------

    def start_session(
        self,
        *,
        agent_version: str | None = None,
        context: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
    ) -> None:
        if self._session_started:
            return
        self._session_started = True
        self._session_start_ts = time.monotonic()
        self._emit(
            OnSessionStartPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                agent_name=self.agent_name,
                agent_version=agent_version,
                context=context or {},
                parent_session_id=parent_session_id,
                source="adapter:claude_sdk",
            )
        )

    def end_session(self, *, success: bool = True) -> None:
        if not self._session_started:
            return
        duration = (
            int((time.monotonic() - self._session_start_ts) * 1000)
            if self._session_start_ts is not None
            else 0
        )
        self._emit(
            OnSessionEndPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                total_duration_ms=duration,
                total_cost_usd=self._total_cost_usd,
                success=success,
                source="adapter:claude_sdk",
            )
        )
        self._session_started = False
        # Rotate so a subsequent start_session uses a fresh id
        self._current_session_id = None

    # ---------- before / after LLM ----------

    def emit_before_llm(self, kwargs: dict[str, Any]) -> None:
        # Auto-start a session if the user didn't call start_session() —
        # the raw SDK has no notion of session boundary, so the first
        # messages.create implicitly opens one.
        if not self._session_started:
            self.start_session()

        # Inspect the user's `messages` array for tool_result blocks that
        # match pending tool calls — pair them into after_tool_use events.
        self._drain_pending_tool_results(kwargs.get("messages") or [])

        model = kwargs.get("model") or "unknown"
        prompt = _summarize_messages(kwargs.get("messages") or [])
        tools = kwargs.get("tools") or []
        self._maybe_sync_profile(kwargs.get("system"))

        # Normalize tools into [{"name", "description"}]
        norm_tools = []
        for t in tools:
            if isinstance(t, dict) and t.get("name"):
                norm_tools.append(
                    {"name": str(t["name"]), "description": t.get("description")}
                )

        self._emit(
            BeforeLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                model=str(model),
                prompt=prompt[:8000],
                tools=norm_tools,
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens"),
                source="adapter:claude_sdk",
            )
        )

    def emit_after_llm(
        self, kwargs: dict[str, Any], response: Any, latency_ms: int
    ) -> None:
        model = (
            getattr(response, "model", None)
            or kwargs.get("model")
            or "unknown"
        )
        response_text = _extract_response_text(response)
        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        cache_read = (
            int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
        )
        cache_write = (
            int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            if usage
            else 0
        )
        cost = _estimate_cost_usd(
            str(model), tokens_in, tokens_out, cache_read, cache_write
        )
        self._total_cost_usd += cost
        self._emit(
            AfterLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                model=str(model),
                response=response_text[:8000],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost_usd=cost,
                latency_ms=latency_ms,
                source="adapter:claude_sdk",
            )
        )
        # Auto-detect tool_use blocks in the response
        self._maybe_emit_tool_use(response)

    def emit_error(self, error_type: str, message: str) -> None:
        self._emit(
            OnErrorPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                error_type=error_type,
                message=message[:2000],
                source="adapter:claude_sdk",
            )
        )

    # ---------- tool_use auto-detection ----------

    def _maybe_emit_tool_use(self, response: Any) -> None:
        """Walk Message.content for ToolUseBlock entries and emit one
        on_agent_decision + before_tool_use per tool the model called."""
        content = _get(response, "content", []) or []
        for block in content:
            btype = _get(block, "type", None)
            if btype != "tool_use":
                continue
            tool_id = str(_get(block, "id", "") or f"tu_{uuid.uuid4().hex[:8]}")
            tool_name = str(_get(block, "name", "") or "tool")
            tool_input = _get(block, "input", {}) or {}
            if not isinstance(tool_input, dict):
                tool_input = {"input": tool_input}
            try:
                args_repr = json.dumps(tool_input)[:1000]
            except Exception:
                args_repr = str(tool_input)[:1000]
            self._emit(
                OnAgentDecisionPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_seq(),
                    decision_type="tool_call",
                    chosen_action=f"{tool_name}({args_repr})",
                    source="adapter:claude_sdk",
                )
            )
            self._emit(
                BeforeToolUsePayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_seq(),
                    tool_name=tool_name,
                    args=tool_input,
                    source="adapter:claude_sdk",
                )
            )
            # Remember this call so we can pair the after_tool_use when
            # the user feeds the tool_result back on the next create call.
            self._pending_tool_calls[tool_id] = (
                tool_name,
                tool_input,
                time.monotonic(),
            )

    def _drain_pending_tool_results(self, messages: list[Any]) -> None:
        """Scan the outgoing `messages` array for tool_result content blocks
        whose `tool_use_id` matches a pending tool call we previously emitted
        a before_tool_use for; emit a paired after_tool_use."""
        if not self._pending_tool_calls or not messages:
            return
        for m in messages:
            content = _get(m, "content", None)
            if not isinstance(content, list):
                continue
            for block in content:
                btype = (
                    block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                )
                if btype != "tool_result":
                    continue
                tu_id = (
                    block.get("tool_use_id")
                    if isinstance(block, dict)
                    else getattr(block, "tool_use_id", None)
                )
                if not tu_id or tu_id not in self._pending_tool_calls:
                    continue
                tool_name, _args, started = self._pending_tool_calls.pop(tu_id)
                duration_ms = int((time.monotonic() - started) * 1000)
                # Extract result text from the block content
                result_content = (
                    block.get("content")
                    if isinstance(block, dict)
                    else getattr(block, "content", None)
                )
                result_text = _flatten_tool_result_content(result_content)
                is_error = (
                    block.get("is_error")
                    if isinstance(block, dict)
                    else getattr(block, "is_error", False)
                )
                self._emit(
                    AfterToolUsePayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_seq(),
                        tool_name=tool_name,
                        result=result_text[:4000],
                        duration_ms=duration_ms,
                        error="tool error" if is_error else None,
                        source="adapter:claude_sdk",
                    )
                )

    def _maybe_sync_profile(self, system: Any) -> None:
        if self._profile_synced:
            return
        text = _normalize_system_param(system)
        if not text:
            return
        client = self._safer or get_client()
        if client is None:
            return
        self._profile_synced = True
        try:
            client.schedule_profile_patch(
                self.agent_id,
                system_prompt=text,
                name=self.agent_name,
            )
        except Exception as e:  # pragma: no cover
            log.debug("SAFER: profile sync failed for %s: %s", self.agent_id, e)

    # ---------- manual helpers (for users who want explicit control) ----------

    def before_tool_use(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        previous_context: str | None = None,
    ) -> None:
        self._emit(
            BeforeToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                tool_name=tool_name,
                args=args or {},
                previous_context=previous_context,
                source="adapter:claude_sdk",
            )
        )

    def after_tool_use(
        self,
        tool_name: str,
        result: Any = None,
        duration_ms: int = 0,
        error: str | None = None,
    ) -> None:
        self._emit(
            AfterToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                tool_name=tool_name,
                result=result,
                duration_ms=duration_ms,
                error=error,
                source="adapter:claude_sdk",
            )
        )

    def agent_decision(
        self,
        decision_type: str,
        reasoning: str | None = None,
        chosen_action: str | None = None,
    ) -> None:
        self._emit(
            OnAgentDecisionPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                decision_type=decision_type,
                reasoning=reasoning,
                chosen_action=chosen_action,
                source="adapter:claude_sdk",
            )
        )

    def final_output(self, response_text: str, total_steps: int = 0) -> None:
        self._emit(
            OnFinalOutputPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                final_response=response_text,
                total_steps=total_steps or self._step_count,
                source="adapter:claude_sdk",
            )
        )


# ---------- proxy-based wrapping (backward compatible) ----------------------


class _MessagesProxy:
    """Wraps `Anthropic().messages` so .create emits hooks (sync)."""

    def __init__(self, messages: Any, emitter: _AnthropicEventEmitter) -> None:
        self._messages = messages
        self._emitter = emitter

    def create(self, **kwargs: Any) -> Any:
        self._emitter.emit_before_llm(kwargs)
        t0 = time.monotonic()
        try:
            response = self._messages.create(**kwargs)
        except Exception as e:
            self._emitter.emit_error("LLMCallError", str(e))
            raise
        latency_ms = int((time.monotonic() - t0) * 1000)
        self._emitter.emit_after_llm(kwargs, response, latency_ms)
        return response

    def stream(self, **kwargs: Any) -> Any:
        """Pass through the underlying stream context manager.

        The SDK's `MessageStreamManager` accumulates events and produces a
        final `Message` via `.get_final_message()`.  We instrument by emitting
        `before_llm_call` on entry and `after_llm_call` on context exit using
        the accumulated final message."""
        self._emitter.emit_before_llm(kwargs)
        t0 = time.monotonic()
        manager = self._messages.stream(**kwargs)
        return _SyncStreamManagerWrapper(manager, self._emitter, kwargs, t0)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._messages, name)


class _AsyncMessagesProxy:
    """Wraps `AsyncAnthropic().messages` so .create emits hooks (async).

    Critical: previous version used a sync proxy on AsyncAnthropic, which
    silently broke — the call returned a coroutine that was never awaited
    so the actual API request never went out, and SAFER emitted bogus
    after_llm_call events with zero tokens / zero text."""

    def __init__(self, messages: Any, emitter: _AnthropicEventEmitter) -> None:
        self._messages = messages
        self._emitter = emitter

    async def create(self, **kwargs: Any) -> Any:
        self._emitter.emit_before_llm(kwargs)
        t0 = time.monotonic()
        try:
            response = await self._messages.create(**kwargs)
        except Exception as e:
            self._emitter.emit_error("LLMCallError", str(e))
            raise
        latency_ms = int((time.monotonic() - t0) * 1000)
        self._emitter.emit_after_llm(kwargs, response, latency_ms)
        return response

    def stream(self, **kwargs: Any) -> Any:
        """`AsyncMessages.stream` is sync `def` returning an async context
        manager.  We wrap that manager so emission happens at __aenter__ /
        __aexit__."""
        self._emitter.emit_before_llm(kwargs)
        t0 = time.monotonic()
        manager = self._messages.stream(**kwargs)
        return _AsyncStreamManagerWrapper(manager, self._emitter, kwargs, t0)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._messages, name)


class _SyncStreamManagerWrapper:
    """Wraps a `MessageStreamManager` to instrument the `with` block exit
    with after_llm_call emission.  Inside the `with`, the user iterates the
    SDK's events normally — we don't try to re-yield them."""

    def __init__(
        self,
        manager: Any,
        emitter: _AnthropicEventEmitter,
        kwargs: dict[str, Any],
        t0: float,
    ) -> None:
        self._manager = manager
        self._emitter = emitter
        self._kwargs = kwargs
        self._t0 = t0
        self._stream: Any = None

    def __enter__(self) -> Any:
        self._stream = self._manager.__enter__()
        return self._stream

    def __exit__(self, exc_type, exc, tb) -> Any:
        try:
            if exc is not None:
                self._emitter.emit_error("LLMStreamError", str(exc))
            else:
                final_msg = None
                try:
                    final_msg = self._stream.get_final_message()
                except Exception:
                    pass
                if final_msg is not None:
                    latency_ms = int((time.monotonic() - self._t0) * 1000)
                    self._emitter.emit_after_llm(self._kwargs, final_msg, latency_ms)
        finally:
            return self._manager.__exit__(exc_type, exc, tb)


class _AsyncStreamManagerWrapper:
    """Async sibling of `_SyncStreamManagerWrapper`."""

    def __init__(
        self,
        manager: Any,
        emitter: _AnthropicEventEmitter,
        kwargs: dict[str, Any],
        t0: float,
    ) -> None:
        self._manager = manager
        self._emitter = emitter
        self._kwargs = kwargs
        self._t0 = t0
        self._stream: Any = None

    async def __aenter__(self) -> Any:
        self._stream = await self._manager.__aenter__()
        return self._stream

    async def __aexit__(self, exc_type, exc, tb) -> Any:
        try:
            if exc is not None:
                self._emitter.emit_error("LLMStreamError", str(exc))
            else:
                final_msg = None
                try:
                    final_msg = await self._stream.get_final_message()
                except Exception:
                    pass
                if final_msg is not None:
                    latency_ms = int((time.monotonic() - self._t0) * 1000)
                    self._emitter.emit_after_llm(
                        self._kwargs, final_msg, latency_ms
                    )
        finally:
            return await self._manager.__aexit__(exc_type, exc, tb)


class AnthropicTracker(_AnthropicEventEmitter):
    """Wraps an `Anthropic` or `AsyncAnthropic` client and exposes SAFER hooks.

    Most attribute access is delegated to the wrapped client, so
    `tracker.models.list()` etc still work.  Only `tracker.messages.create()`
    and `tracker.messages.stream()` are instrumented."""

    def __init__(
        self,
        client: Any,
        agent_id: str,
        agent_name: str | None = None,
        session_id: str | None = None,
        safer: SaferClient | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            agent_name=agent_name,
            session_id=session_id,
            safer_client=safer,
        )
        self._client = client
        # Detect async client via the SDK's marker classes
        is_async = _is_async_anthropic(client)
        if is_async:
            self.messages = _AsyncMessagesProxy(client.messages, self)
        else:
            self.messages = _MessagesProxy(client.messages, self)

    def __getattr__(self, name: str) -> Any:
        # Delegate non-instrumented attributes to the wrapped client.
        return getattr(self._client, name)


def wrap_anthropic(
    client: Any,
    *,
    agent_id: str,
    agent_name: str | None = None,
    session_id: str | None = None,
) -> AnthropicTracker:
    """Wrap an `Anthropic` or `AsyncAnthropic` client with SAFER instrumentation.

    The returned `AnthropicTracker` works as a drop-in proxy: attribute
    access falls through to the underlying client; only `messages.create`
    and `messages.stream` are instrumented to emit SAFER events.

    Async clients are detected automatically and use an async-aware proxy
    so `await client.messages.create(...)` works correctly."""
    return AnthropicTracker(
        client=client,
        agent_id=agent_id,
        agent_name=agent_name,
        session_id=session_id,
    )


# ---------- native subclass (cleaner for new code) -------------------------


def _build_safer_anthropic_classes() -> tuple[type, type]:
    """Build `SaferAnthropic` and `SaferAsyncAnthropic` lazily; cached.

    These are subclasses of the real `Anthropic` / `AsyncAnthropic` SDK
    clients with their `messages` resource swapped for instrumented
    versions.  The cached_property override is the official Stainless
    extension point and is robust to SDK regenerations."""
    try:
        from anthropic import (
            Anthropic as _Anthropic,
            AsyncAnthropic as _AsyncAnthropic,
        )
        from anthropic.resources.messages.messages import (
            AsyncMessages as _AsyncMessages,
            Messages as _Messages,
        )
    except ImportError as e:
        raise ImportError(
            "SaferAnthropic requires the `anthropic` SDK (>=0.40)."
        ) from e

    class _SaferMessages(_Messages):  # type: ignore[misc, valid-type]
        """`Messages` subclass that emits SAFER events around `.create` /
        `.stream`.  Constructed with the parent client and shares its
        SAFER emitter via the closure-style `_safer_emitter` attribute on
        the parent client."""

        _safer_emitter: _AnthropicEventEmitter

        def create(self, **kwargs: Any) -> Any:  # type: ignore[override]
            emitter = self._safer_emitter
            emitter.emit_before_llm(kwargs)
            t0 = time.monotonic()
            try:
                response = super().create(**kwargs)
            except Exception as e:
                emitter.emit_error("LLMCallError", str(e))
                raise
            latency_ms = int((time.monotonic() - t0) * 1000)
            emitter.emit_after_llm(kwargs, response, latency_ms)
            return response

        def stream(self, **kwargs: Any) -> Any:  # type: ignore[override]
            emitter = self._safer_emitter
            emitter.emit_before_llm(kwargs)
            t0 = time.monotonic()
            manager = super().stream(**kwargs)
            return _SyncStreamManagerWrapper(manager, emitter, kwargs, t0)

    class _SaferAsyncMessages(_AsyncMessages):  # type: ignore[misc, valid-type]
        _safer_emitter: _AnthropicEventEmitter

        async def create(self, **kwargs: Any) -> Any:  # type: ignore[override]
            emitter = self._safer_emitter
            emitter.emit_before_llm(kwargs)
            t0 = time.monotonic()
            try:
                response = await super().create(**kwargs)
            except Exception as e:
                emitter.emit_error("LLMCallError", str(e))
                raise
            latency_ms = int((time.monotonic() - t0) * 1000)
            emitter.emit_after_llm(kwargs, response, latency_ms)
            return response

        def stream(self, **kwargs: Any) -> Any:  # type: ignore[override]
            emitter = self._safer_emitter
            emitter.emit_before_llm(kwargs)
            t0 = time.monotonic()
            manager = super().stream(**kwargs)
            return _AsyncStreamManagerWrapper(manager, emitter, kwargs, t0)

    class _SaferAnthropic(_Anthropic):  # type: ignore[misc, valid-type]
        """`Anthropic` subclass with SAFER instrumentation on `messages`.

        All other attribute access falls through to the parent class via
        normal MRO — so `client.models.list()` and the rest of the SDK
        surface continue to work."""

        def __init__(
            self,
            *,
            agent_id: str,
            agent_name: str | None = None,
            session_id: str | None = None,
            safer_client: SaferClient | None = None,
            **kwargs: Any,
        ) -> None:
            super().__init__(**kwargs)
            self._safer_emitter = _AnthropicEventEmitter(
                agent_id=agent_id,
                agent_name=agent_name,
                session_id=session_id,
                safer_client=safer_client,
            )

        @cached_property
        def messages(self) -> _SaferMessages:  # type: ignore[override]
            m = _SaferMessages(self)
            m._safer_emitter = self._safer_emitter
            return m

        # Convenience pass-through to the emitter for manual events
        def start_session(self, **kw: Any) -> None:
            self._safer_emitter.start_session(**kw)

        def end_session(self, *, success: bool = True) -> None:
            self._safer_emitter.end_session(success=success)

        def final_output(self, text: str, total_steps: int = 0) -> None:
            self._safer_emitter.final_output(text, total_steps)

    class _SaferAsyncAnthropic(_AsyncAnthropic):  # type: ignore[misc, valid-type]
        def __init__(
            self,
            *,
            agent_id: str,
            agent_name: str | None = None,
            session_id: str | None = None,
            safer_client: SaferClient | None = None,
            **kwargs: Any,
        ) -> None:
            super().__init__(**kwargs)
            self._safer_emitter = _AnthropicEventEmitter(
                agent_id=agent_id,
                agent_name=agent_name,
                session_id=session_id,
                safer_client=safer_client,
            )

        @cached_property
        def messages(self) -> _SaferAsyncMessages:  # type: ignore[override]
            m = _SaferAsyncMessages(self)
            m._safer_emitter = self._safer_emitter
            return m

        def start_session(self, **kw: Any) -> None:
            self._safer_emitter.start_session(**kw)

        def end_session(self, *, success: bool = True) -> None:
            self._safer_emitter.end_session(success=success)

        def final_output(self, text: str, total_steps: int = 0) -> None:
            self._safer_emitter.final_output(text, total_steps)

    return _SaferAnthropic, _SaferAsyncAnthropic


_CACHED_SUBCLASSES: tuple[type, type] | None = None


def _get_safer_anthropic_classes() -> tuple[type, type]:
    global _CACHED_SUBCLASSES
    if _CACHED_SUBCLASSES is None:
        _CACHED_SUBCLASSES = _build_safer_anthropic_classes()
    return _CACHED_SUBCLASSES


class SaferAnthropic:
    """Native `Anthropic` subclass with SAFER instrumentation.

    Use this for new code:

        client = SaferAnthropic(agent_id="...", agent_name="...", api_key=...)
        response = client.messages.create(model="claude-opus-4-7", ...)

    The class is built lazily on first instantiation so importing this
    module is safe even when `anthropic` isn't installed."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        sync_cls, _ = _get_safer_anthropic_classes()
        return sync_cls(*args, **kwargs)


class SaferAsyncAnthropic:
    """Async sibling of `SaferAnthropic`."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        _, async_cls = _get_safer_anthropic_classes()
        return async_cls(*args, **kwargs)


# ---------- helpers ---------------------------------------------------------


def _is_async_anthropic(client: Any) -> bool:
    """Return True if the given client is an `AsyncAnthropic` (or subclass).

    Tries `isinstance(client, AsyncAnthropic)` first.  If the SDK isn't
    importable or the isinstance check returns False, falls back to duck
    typing: an async client's `messages.create` is a coroutine function.
    Both checks run, so subclasses or test doubles are detected too."""
    import inspect

    try:
        from anthropic import AsyncAnthropic

        if isinstance(client, AsyncAnthropic):
            return True
    except ImportError:
        pass
    # Duck typing — covers fakes and any other subclass we can't import
    msgs = getattr(client, "messages", None)
    create = getattr(msgs, "create", None) if msgs is not None else None
    return inspect.iscoroutinefunction(create) if create else False


def _summarize_messages(messages: list[Any]) -> str:
    """Best-effort stringify of messages list for the before_llm_call prompt field."""
    if not messages:
        return ""
    lines: list[str] = []
    for m in messages:
        role = _get(m, "role", "user")
        content = _get(m, "content", "")
        if isinstance(content, list):
            text_chunks: list[str] = []
            for block in content:
                btype = _get(block, "type", None)
                if btype == "text":
                    text_chunks.append(_get(block, "text", "") or "")
                elif btype == "tool_use":
                    name = _get(block, "name", "") or ""
                    text_chunks.append(f"[tool_use:{name}]")
                elif btype == "tool_result":
                    text_chunks.append("[tool_result]")
                # image / other → skipped
            content_str = "\n".join(text_chunks)
        else:
            content_str = str(content)
        lines.append(f"[{role}] {content_str}")
    return "\n".join(lines)


def _extract_response_text(response: Any) -> str:
    """Pull text from an `anthropic.types.Message`, tolerant to shape."""
    content = _get(response, "content", []) or []
    parts: list[str] = []
    for block in content:
        btype = _get(block, "type", None)
        if btype == "text":
            text = _get(block, "text", "") or ""
            if text:
                parts.append(text)
        elif btype == "tool_use":
            name = _get(block, "name", "") or ""
            parts.append(f"[tool_use:{name}]")
    return "\n".join(parts)


def _flatten_tool_result_content(content: Any) -> str:
    """Anthropic `tool_result` blocks have `content` as a string OR a list of
    text/image blocks.  Extract plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            btype = (
                block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            )
            if btype == "text":
                t = (
                    block.get("text")
                    if isinstance(block, dict)
                    else getattr(block, "text", None)
                )
                if t:
                    parts.append(str(t))
        return "\n".join(parts)
    return str(content)


def _get(obj: Any, attr: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _normalize_system_param(system: Any) -> str:
    """Turn Anthropic's `system=` (str or list of text blocks) into one string."""
    if system is None:
        return ""
    if isinstance(system, str):
        return system.strip()
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            btype = (
                block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            )
            if btype == "text":
                text = (
                    block.get("text")
                    if isinstance(block, dict)
                    else getattr(block, "text", None)
                )
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return str(system).strip()


def _reset_for_tests() -> None:
    global _CACHED_SUBCLASSES
    _CACHED_SUBCLASSES = None


__all__ = [
    "SaferAnthropic",
    "SaferAsyncAnthropic",
    "AnthropicTracker",
    "wrap_anthropic",
]
