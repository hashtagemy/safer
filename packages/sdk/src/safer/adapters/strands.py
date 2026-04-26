"""Strands Agents adapter — bridges the `HookProvider` system onto
SAFER's 9-hook lifecycle.

Strands (`pip install strands-agents`) exposes `HookProvider` as the
canonical extension point for cross-cutting concerns. From the
`strands.hooks` module docstring:

    The hook system enables both built-in SDK components and user code
    to react to or modify agent behavior through strongly-typed event
    callbacks... This approach replaces older callback mechanisms with
    a more composable, type-safe design.

A `HookProvider` implements a single method, `register_hooks(registry)`,
and registers callbacks for the events it cares about. We handle eight
single-agent events; multi-agent events are out of scope for this
adapter.

### Two-line integration

```python
from strands import Agent
from strands.models.anthropic import AnthropicModel
from safer.adapters.strands import SaferHookProvider

agent = Agent(
    model=AnthropicModel(model_id="claude-opus-4-7"),
    tools=[...],
    hooks=[SaferHookProvider(agent_id="system_diag",
                              agent_name="System Diagnostic")],
)
```

`SaferHookProvider.__init__` calls `ensure_runtime(...)` so the user
does not have to call `instrument()` separately.

Strands is an optional dependency — this module is import-safe even
when `strands-agents` is missing; the real provider class is built on
first instantiation.
"""

from __future__ import annotations

import logging
import time
import uuid
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
from ..exceptions import SaferBlocked
from ..gateway_check import check_or_raise
from ._bootstrap import ensure_runtime

log = logging.getLogger("safer.adapters.strands")


# ---------- pricing — delegated to safer._pricing (Bedrock aliases handled there)


def _estimate_cost(
    model: str, tokens_in: int, tokens_out: int, cache_read: int = 0
) -> float:
    """Estimate USD cost via the shared pricing table; 0.0 for unknown models.

    Strands users can wire any Anthropic model — direct (`claude-opus-4-7`)
    or Bedrock-hosted (`anthropic.claude-opus-4-7-v1:0`).  The `_pricing`
    module's `match_model` resolves both to the same pricing entry."""
    from .._pricing import estimate_cost

    cost = estimate_cost(
        model, tokens_in=tokens_in, tokens_out=tokens_out, cache_read=cache_read
    )
    return cost or 0.0


# ---------- content helpers --------------------------------------------


def _safe_str(obj: Any, limit: int = 8000) -> str:
    if obj is None:
        return ""
    s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


def _message_text(message: Any) -> str:
    """Extract plain text from a Strands `Message` (`{role, content}`
    dict). `content` is a list of content blocks (`text`, `toolUse`,
    `toolResult`, ...)."""
    if message is None:
        return ""
    content = (
        message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    )
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if "text" in block and isinstance(block["text"], str):
                parts.append(block["text"])
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _message_tool_uses(message: Any) -> list[tuple[str, dict[str, Any]]]:
    """Return `(tool_name, input)` for every toolUse block in a
    Message. Used to synthesize `on_agent_decision`."""
    if message is None:
        return []
    content = (
        message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    )
    if not content:
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for block in content:
        tu = None
        if isinstance(block, dict):
            tu = block.get("toolUse")
        else:
            tu = getattr(block, "toolUse", None)
        if tu is None:
            continue
        name = (
            tu.get("name")
            if isinstance(tu, dict)
            else getattr(tu, "name", None)
        )
        raw_input = (
            tu.get("input")
            if isinstance(tu, dict)
            else getattr(tu, "input", None)
        )
        if name:
            out.append((str(name), dict(raw_input or {})))
    return out


def _tool_result_text(result: Any) -> str:
    """Flatten a Strands `ToolResult.content` (list of ToolResultContent
    blocks) into a string."""
    if result is None:
        return ""
    content = (
        result.get("content") if isinstance(result, dict) else getattr(result, "content", None)
    )
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            for key in ("text", "json"):
                if key in block:
                    parts.append(_safe_str(block[key]))
                    break
            else:
                parts.append(_safe_str(block))
        else:
            parts.append(_safe_str(block))
    return "\n".join(parts)


def _agent_tool_names(agent: Any) -> list[dict[str, Any]]:
    """Return SAFER tool descriptors for every tool registered on the agent.

    Strands `Agent` exposes `agent.tool_names` directly (a list of registered
    tool names) — that's the canonical source.  Falls back to walking
    `agent.tool_registry.registry` for older Strands versions or duck types
    that don't expose `tool_names`."""
    direct = getattr(agent, "tool_names", None)
    if isinstance(direct, (list, tuple, set)):
        return [{"name": str(n)} for n in direct]
    registry = getattr(agent, "tool_registry", None)
    if registry is None:
        return []
    config = getattr(registry, "registry", None)
    if isinstance(config, dict):
        return [{"name": str(k)} for k in config]
    return []


def _agent_model_id(agent: Any) -> str:
    model = getattr(agent, "model", None)
    if model is None:
        return "claude"
    for attr in ("model_id", "id", "name"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            return value
    config = getattr(model, "config", None)
    if isinstance(config, dict):
        for key in ("model_id", "model", "name"):
            v = config.get(key)
            if isinstance(v, str) and v:
                return v
    return "claude"


def _usage_from_agent(agent: Any) -> tuple[int, int, int]:
    """Best-effort: pull cumulative usage from Strands' event-loop
    metrics. Strands does not include token counts on the
    `BeforeModelCallEvent` / `AfterModelCallEvent` directly."""
    metrics = getattr(agent, "event_loop_metrics", None)
    if metrics is None:
        return 0, 0, 0
    usage = getattr(metrics, "accumulated_usage", None) or getattr(
        metrics, "usage", None
    )
    if usage is None:
        return 0, 0, 0
    get = usage.get if isinstance(usage, dict) else lambda k: getattr(usage, k, 0)
    tokens_in = int(get("inputTokens") or get("input_tokens") or 0)
    tokens_out = int(get("outputTokens") or get("output_tokens") or 0)
    cache_read = int(
        get("cacheReadInputTokens") or get("cache_read_input_tokens") or 0
    )
    return tokens_in, tokens_out, cache_read


# ---------- lazy loader for the Strands hook API -----------------------


def _import_hooks() -> dict[str, type]:
    try:
        from strands.hooks import (  # type: ignore[import-not-found]
            AfterInvocationEvent,
            AfterModelCallEvent,
            AfterToolCallEvent,
            AgentInitializedEvent,
            BeforeInvocationEvent,
            BeforeModelCallEvent,
            BeforeToolCallEvent,
            HookProvider,
            HookRegistry,
            MessageAddedEvent,
        )
    except ImportError as e:
        raise ImportError(
            "SaferHookProvider requires `strands-agents`. "
            "Install with `pip install strands-agents` or "
            "`pip install 'safer-sdk[strands]'`."
        ) from e
    return {
        "HookProvider": HookProvider,
        "HookRegistry": HookRegistry,
        "AgentInitializedEvent": AgentInitializedEvent,
        "BeforeInvocationEvent": BeforeInvocationEvent,
        "AfterInvocationEvent": AfterInvocationEvent,
        "MessageAddedEvent": MessageAddedEvent,
        "BeforeModelCallEvent": BeforeModelCallEvent,
        "AfterModelCallEvent": AfterModelCallEvent,
        "BeforeToolCallEvent": BeforeToolCallEvent,
        "AfterToolCallEvent": AfterToolCallEvent,
    }


# ---------- the real provider, built at first instantiation ------------


def _make_provider_cls() -> type:
    api = _import_hooks()
    HookProvider = api["HookProvider"]

    class _SaferHookProvider(HookProvider):  # type: ignore[misc, valid-type]
        """HookProvider implementation that forwards eight Strands
        events to SAFER's 9-hook event model."""

        def __init__(
            self,
            *,
            agent_id: str,
            agent_name: str | None = None,
            session_id: str | None = None,
            client: SaferClient | None = None,
            pin_session: bool = False,
        ) -> None:
            ensure_runtime(agent_id, agent_name, framework="strands")
            self.agent_id = agent_id
            self.agent_name = agent_name or agent_id
            # `_initial_session_id` pins the FIRST invocation only.  Every
            # subsequent `agent(prompt)` call (i.e. each Strands invocation)
            # gets a fresh SAFER session_id via `_begin_invocation` —
            # otherwise multiple turns would all write to the same session.
            #
            # `pin_session=True` flips that: every invocation reuses the
            # same SAFER session_id and we defer `on_session_end` to
            # process exit (atexit). Useful for chat REPLs where the
            # whole conversation is one logical session.
            self._initial_session_id = session_id
            self._current_session_id: str | None = None
            self._client = client
            self._pin_session = pin_session
            self._session_started = False
            self._sequence = 0
            self._step_count = 0
            self._model_start_ts: float | None = None
            self._tool_start_ts: dict[str, float] = {}
            self._profile_synced = False
            self._last_tokens: tuple[int, int, int] = (0, 0, 0)
            self._session_start_ts: float | None = None
            self._total_cost_usd: float = 0.0
            self._atexit_registered = False
            if pin_session:
                self._register_atexit_close()

        @property
        def session_id(self) -> str:
            """Current SAFER session id (rotates per Strands invocation)."""
            if self._current_session_id is None:
                self._current_session_id = (
                    self._initial_session_id or f"sess_{uuid.uuid4().hex[:16]}"
                )
            return self._current_session_id

        def _begin_invocation(self) -> None:
            """Start a fresh SAFER session for a new Strands invocation.

            When `pin_session=True` this is a soft reset: the session_id
            is preserved, `_session_started` stays True (so we don't fire
            another on_session_start), and only the per-invocation
            counters/timers are zeroed for accurate per-step accounting.
            """
            if self._pin_session:
                # Keep session_id + session_started; reset per-invocation
                # bookkeeping so each turn's deltas are independent.
                self._sequence += 0  # session-wide sequence keeps growing
                self._model_start_ts = None
                self._tool_start_ts.clear()
                return
            self._current_session_id = f"sess_{uuid.uuid4().hex[:16]}"
            self._session_started = False
            self._sequence = 0
            self._step_count = 0
            self._model_start_ts = None
            self._tool_start_ts.clear()
            self._last_tokens = (0, 0, 0)
            self._session_start_ts = time.monotonic()
            self._total_cost_usd = 0.0

        def _register_atexit_close(self) -> None:
            """Wire an atexit hook that emits on_session_end exactly once.

            Only used in pin_session mode — without it, a chat REPL that
            runs through `Ctrl+D` would never close its session in SAFER.
            """
            if self._atexit_registered:
                return
            import atexit

            atexit.register(self._atexit_close_session)
            self._atexit_registered = True

        def _atexit_close_session(self) -> None:
            """Idempotent atexit cleanup for pin_session mode."""
            if not self._pin_session or not self._session_started:
                return
            try:
                duration_ms = (
                    int((time.monotonic() - self._session_start_ts) * 1000)
                    if self._session_start_ts is not None
                    else 0
                )
                self._emit(
                    OnSessionEndPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        total_duration_ms=duration_ms,
                        total_cost_usd=self._total_cost_usd,
                        success=True,
                    )
                )
                self._session_started = False
            except Exception:  # pragma: no cover — atexit must never raise
                pass

        def close_session(self, *, success: bool = True) -> None:
            """Manually emit on_session_end for the pinned chat session.

            No-op when `pin_session=False` (the per-invocation lifecycle
            already closed the session)."""
            if not self._pin_session or not self._session_started:
                return
            duration_ms = (
                int((time.monotonic() - self._session_start_ts) * 1000)
                if self._session_start_ts is not None
                else 0
            )
            self._emit(
                OnSessionEndPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    total_duration_ms=duration_ms,
                    total_cost_usd=self._total_cost_usd,
                    success=success,
                )
            )
            self._session_started = False

        # internal plumbing ----------------------------------------

        def _get_client(self) -> SaferClient | None:
            return self._client or get_client()

        def _next_sequence(self) -> int:
            seq = self._sequence
            self._sequence += 1
            return seq

        def _emit(self, payload: Any) -> None:
            client = self._get_client()
            if client is None:
                return
            try:
                client.track_event(
                    payload.hook,
                    payload.model_dump(mode="json"),
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                )
            except Exception as e:  # pragma: no cover
                log.debug("strands adapter emit failed: %s", e)

        def _emit_error(self, err: BaseException, kind: str = "error") -> None:
            try:
                self._emit(
                    OnErrorPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        error_type=f"{type(err).__name__}:{kind}",
                        message=str(err)[:2000],
                    )
                )
            except Exception:  # pragma: no cover
                pass

        def _ensure_session_started(
            self, context: dict[str, Any] | None = None
        ) -> None:
            if self._session_started:
                return
            self._session_started = True
            self._emit(
                OnSessionStartPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    agent_name=self.agent_name,
                    context=context or {},
                )
            )

        def _maybe_sync_profile(self, agent: Any) -> None:
            if self._profile_synced:
                return
            prompt = getattr(agent, "system_prompt", None)
            if not isinstance(prompt, str) or not prompt.strip():
                return
            client = self._get_client()
            if client is None:
                return
            self._profile_synced = True
            try:
                client.schedule_profile_patch(
                    self.agent_id,
                    system_prompt=prompt.strip(),
                    name=self.agent_name,
                )
            except Exception as e:  # pragma: no cover
                log.debug("strands profile sync failed: %s", e)

        # event handlers ------------------------------------------

        def _on_agent_initialized(self, event: Any) -> None:
            # No-op: sync-only event, profile sync happens at first
            # BeforeModelCall where system_prompt is authoritative.
            return None

        def _on_before_invocation(self, event: Any) -> None:
            try:
                # Each Strands invocation = one SAFER session.  Rotate the
                # session_id here so multiple `agent(prompt)` calls on the
                # same hook provider produce distinct backend sessions.
                self._begin_invocation()
                self._ensure_session_started()
            except Exception as e:
                self._emit_error(e, "before_invocation")

        def _on_after_invocation(self, event: Any) -> None:
            try:
                result = getattr(event, "result", None)
                final_text = ""
                if result is not None:
                    final_text = _message_text(getattr(result, "message", None))
                self._emit(
                    OnFinalOutputPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        final_response=_safe_str(final_text, 4000),
                        total_steps=self._step_count,
                    )
                )
                # pin_session=True keeps the session open across
                # invocations — the atexit hook will close it once.
                if self._pin_session:
                    return
                duration_ms = (
                    int((time.monotonic() - self._session_start_ts) * 1000)
                    if self._session_start_ts is not None
                    else 0
                )
                self._emit(
                    OnSessionEndPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        total_duration_ms=duration_ms,
                        total_cost_usd=self._total_cost_usd,
                        success=True,
                    )
                )
                self._session_started = False
            except Exception as e:
                self._emit_error(e, "after_invocation")

        def _on_message_added(self, event: Any) -> None:
            try:
                message = getattr(event, "message", None)
                if message is None:
                    return
                role = (
                    message.get("role")
                    if isinstance(message, dict)
                    else getattr(message, "role", None)
                )
                if role != "assistant":
                    return
                tool_uses = _message_tool_uses(message)
                for tool_name, _ in tool_uses:
                    self._emit(
                        OnAgentDecisionPayload(
                            session_id=self.session_id,
                            agent_id=self.agent_id,
                            sequence=self._next_sequence(),
                            decision_type="tool_call",
                            chosen_action=tool_name,
                        )
                    )
            except Exception as e:
                self._emit_error(e, "message_added")

        def _on_before_model_call(self, event: Any) -> None:
            try:
                self._ensure_session_started()
                agent = getattr(event, "agent", None)
                self._maybe_sync_profile(agent)
                model = _agent_model_id(agent)
                messages = getattr(agent, "messages", None) or []
                prompt = _message_text(messages[-1]) if messages else ""
                tools = _agent_tool_names(agent)
                self._model_start_ts = time.monotonic()
                self._emit(
                    BeforeLLMCallPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        model=model,
                        prompt=prompt[:8000],
                        tools=tools,
                    )
                )
            except Exception as e:
                self._emit_error(e, "before_model_call")

        def _on_after_model_call(self, event: Any) -> None:
            try:
                agent = getattr(event, "agent", None)
                started = self._model_start_ts
                self._model_start_ts = None
                latency_ms = (
                    int((time.monotonic() - started) * 1000) if started else 0
                )
                model = _agent_model_id(agent)
                stop_response = getattr(event, "stop_response", None)
                response_text = ""
                if stop_response is not None:
                    response_text = _message_text(
                        getattr(stop_response, "message", None)
                    )
                tokens_in, tokens_out, cache_read = _usage_from_agent(agent)
                # Metrics are cumulative; report delta for this step.
                prev = self._last_tokens
                delta_in = max(0, tokens_in - prev[0])
                delta_out = max(0, tokens_out - prev[1])
                delta_cache = max(0, cache_read - prev[2])
                self._last_tokens = (tokens_in, tokens_out, cache_read)
                cost = _estimate_cost(model, delta_in, delta_out, delta_cache)
                self._total_cost_usd += cost
                self._emit(
                    AfterLLMCallPayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        model=model,
                        response=response_text[:8000],
                        tokens_in=delta_in,
                        tokens_out=delta_out,
                        cache_read_tokens=delta_cache,
                        cost_usd=cost,
                        latency_ms=latency_ms,
                    )
                )
                self._step_count += 1
                exception = getattr(event, "exception", None)
                if exception is not None:
                    self._emit_error(exception, "model_error")
            except Exception as e:
                self._emit_error(e, "after_model_call")

        def _on_before_tool_call(self, event: Any) -> None:
            tool_name: str = "tool"
            tool_input: dict[str, Any] = {}
            try:
                self._ensure_session_started()
                tool_use = getattr(event, "tool_use", None)
                tool_name = (
                    tool_use.get("name")
                    if isinstance(tool_use, dict)
                    else getattr(tool_use, "name", None)
                ) or "tool"
                tool_input = (
                    tool_use.get("input")
                    if isinstance(tool_use, dict)
                    else getattr(tool_use, "input", None)
                ) or {}
                tool_use_id = (
                    tool_use.get("toolUseId")
                    if isinstance(tool_use, dict)
                    else getattr(tool_use, "toolUseId", None)
                ) or uuid.uuid4().hex
                self._tool_start_ts[tool_use_id] = time.monotonic()
                self._emit(
                    BeforeToolUsePayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        tool_name=str(tool_name),
                        args=dict(tool_input),
                    )
                )
            except Exception as e:
                self._emit_error(e, "before_tool_call")

            # Synchronous gateway check — runs AFTER the event emit so
            # the dashboard sees the attempted call even when blocked.
            # We use Strands' `cancel_tool` field (per
            # `BeforeToolCallEvent` contract) instead of raising: a raw
            # exception leaves the tool_use without a matching
            # tool_result, which the next LLM turn rejects with a 400.
            # `cancel_tool` makes Strands synthesize an error tool_result
            # so the conversation stays consistent and the assistant can
            # explain the block.
            try:
                check_or_raise(
                    "before_tool_use",
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    tool_name=str(tool_name),
                    args=dict(tool_input),
                )
            except SaferBlocked as blocked:
                try:
                    event.cancel_tool = blocked.message  # type: ignore[attr-defined]
                except Exception:  # pragma: no cover — older Strands
                    raise
            except Exception as e:  # pragma: no cover — helper already soft-fails
                self._emit_error(e, "gateway_check")

        def _on_after_tool_call(self, event: Any) -> None:
            try:
                tool_use = getattr(event, "tool_use", None)
                tool_name = (
                    tool_use.get("name")
                    if isinstance(tool_use, dict)
                    else getattr(tool_use, "name", None)
                ) or "tool"
                tool_use_id = (
                    tool_use.get("toolUseId")
                    if isinstance(tool_use, dict)
                    else getattr(tool_use, "toolUseId", None)
                )
                started = (
                    self._tool_start_ts.pop(tool_use_id, None)
                    if tool_use_id
                    else None
                )
                duration_ms = (
                    int((time.monotonic() - started) * 1000) if started else 0
                )
                result = getattr(event, "result", None)
                self._emit(
                    AfterToolUsePayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_sequence(),
                        tool_name=str(tool_name),
                        result=_safe_str(_tool_result_text(result), 4000),
                        duration_ms=duration_ms,
                    )
                )
                self._step_count += 1
                exception = getattr(event, "exception", None)
                status = None
                if isinstance(result, dict):
                    status = result.get("status")
                else:
                    status = getattr(result, "status", None)
                if exception is not None:
                    self._emit_error(exception, "tool_error")
                elif status == "error":
                    self._emit_error(
                        RuntimeError(_tool_result_text(result) or "tool returned error"),
                        "tool_error",
                    )
            except Exception as e:
                self._emit_error(e, "after_tool_call")

        # register_hooks ------------------------------------------

        def register_hooks(self, registry: Any, **kwargs: Any) -> None:
            registry.add_callback(api["AgentInitializedEvent"], self._on_agent_initialized)
            registry.add_callback(api["BeforeInvocationEvent"], self._on_before_invocation)
            registry.add_callback(api["AfterInvocationEvent"], self._on_after_invocation)
            registry.add_callback(api["MessageAddedEvent"], self._on_message_added)
            registry.add_callback(api["BeforeModelCallEvent"], self._on_before_model_call)
            registry.add_callback(api["AfterModelCallEvent"], self._on_after_model_call)
            registry.add_callback(api["BeforeToolCallEvent"], self._on_before_tool_call)
            registry.add_callback(api["AfterToolCallEvent"], self._on_after_tool_call)

    return _SaferHookProvider


# ---------- public wrapper ---------------------------------------------


class SaferHookProvider:  # type: ignore[no-redef]
    """HookProvider that bridges Strands events into SAFER's 9-hook
    lifecycle. Pass an instance via `Agent(..., hooks=[...])`.

    The concrete class is built on first instantiation so
    `from safer.adapters.strands import SaferHookProvider` never fails
    when `strands-agents` is missing.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        real = _make_provider_cls()
        return real(*args, **kwargs)


__all__ = ["SaferHookProvider"]
