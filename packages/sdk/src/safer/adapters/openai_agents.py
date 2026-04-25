"""OpenAI Agents SDK adapter — first-class native hook integration.

The `openai-agents` package (`pip install openai-agents`) is OpenAI's
multi-agent framework with `Agent`, `Runner`, `function_tool`, etc.  It
exposes two extension points:

  * `RunHooks` — global lifecycle (`on_llm_start/end`, `on_agent_start/end`,
    `on_tool_start/end`, `on_handoff`).  Attached per `Runner.run(...)`.
  * `TracingProcessor` — span-level telemetry (`on_span_start/end`,
    `on_trace_start/end`).  Registered globally via `add_trace_processor`.

Usage:

    from agents import Agent, Runner
    from safer.adapters.openai_agents import install_safer_for_agents

    hooks = install_safer_for_agents(agent_id="repo_analyst",
                                     agent_name="Repo Analyst")
    agent = Agent(name="repo_analyst", instructions="...", tools=[...])
    result = await Runner.run(agent, "task", hooks=hooks)

`install_safer_for_agents` registers a `SaferTracingProcessor` globally
(idempotent — calling twice is fine) and returns a `SaferRunHooks`
instance the caller passes per-run.

Backward compat: `wrap_openai` is re-exported from `openai_client` for
users who only need the raw OpenAI client wrapper, not the Agents SDK.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Iterable

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
from .openai_client import wrap_openai  # backward compat re-export

log = logging.getLogger("safer.adapters.openai_agents")


# ---------- pricing ----------------------------------------------------------


def _estimate_cost(
    model: str, tokens_in: int, tokens_out: int, cache_read: int = 0
) -> float:
    from .._pricing import estimate_cost

    cost = estimate_cost(
        model, tokens_in=tokens_in, tokens_out=tokens_out, cache_read=cache_read
    )
    return cost or 0.0


def _safe_str(obj: Any, limit: int = 4000) -> str:
    if obj is None:
        return ""
    s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


# ---------- helpers extracted from Agents SDK objects -----------------------


def _agent_model(agent: Any) -> str:
    """Extract the model name from an `Agent` instance."""
    model = getattr(agent, "model", None)
    if isinstance(model, str):
        return model
    if model is not None:
        for attr in ("model_id", "id", "name"):
            v = getattr(model, attr, None)
            if isinstance(v, str) and v:
                return v
    return "gpt-4o"


def _agent_name(agent: Any) -> str:
    return str(getattr(agent, "name", "agent"))


def _agent_tool_names(agent: Any) -> list[dict[str, Any]]:
    """Return SAFER tool descriptors for an Agent's `tools`."""
    out = []
    for t in getattr(agent, "tools", None) or []:
        name = getattr(t, "name", None) or getattr(t, "__name__", None) or "tool"
        desc = getattr(t, "description", None)
        out.append({"name": str(name), "description": _safe_str(desc, 200) or None})
    return out


def _input_items_to_prompt(input_items: Any) -> str:
    """Best-effort prompt preview from `RunHooks.on_llm_start` input_items.

    Items are heterogeneous TypedDict / Pydantic shapes from the Responses API
    input vocabulary.  We extract text from the common ones and skip the rest."""
    if not input_items:
        return ""
    parts: list[str] = []
    for item in input_items:
        # Handle dict and pydantic model alike
        get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)  # noqa: E731
        role = get("role")
        content = get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text_parts = []
            for c in content:
                cget = c.get if isinstance(c, dict) else lambda k, d=None, cc=c: getattr(cc, k, d)  # noqa: E731
                ctype = cget("type", "")
                if ctype in ("input_text", "output_text", "text"):
                    t = cget("text", "")
                    if t:
                        text_parts.append(str(t))
            text = "".join(text_parts)
        else:
            text = ""
        if text:
            parts.append(f"[{role or 'user'}] {text}")
    return "\n".join(parts)[:8000]


def _model_response_text(response: Any) -> str:
    """Extract text from an Agents SDK `ModelResponse`.

    `ModelResponse.output` is a list of `ResponseOutputItem` with the usual
    Responses-API shape; `output_text` aggregates if available."""
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text:
        return text
    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        itype = getattr(item, "type", None)
        if itype != "message":
            continue
        for block in getattr(item, "content", None) or []:
            btype = getattr(block, "type", None)
            t = getattr(block, "text", None)
            if btype == "output_text" and t:
                parts.append(str(t))
    return "\n".join(parts)


def _model_response_usage(response: Any) -> tuple[int, int, int]:
    """Pull (in, out, cache_read) tokens from an Agents SDK ModelResponse."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    # Multiple field shapes — Agents SDK normalizes to Pydantic; some
    # versions use `input_tokens`, others `prompt_tokens`.
    tokens_in = int(
        getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", 0) or 0
    )
    tokens_out = int(
        getattr(usage, "output_tokens", None)
        or getattr(usage, "completion_tokens", 0)
        or 0
    )
    cache_read = 0
    details = getattr(usage, "input_tokens_details", None) or getattr(
        usage, "prompt_tokens_details", None
    )
    if details:
        cache_read = int(getattr(details, "cached_tokens", 0) or 0)
    return tokens_in, tokens_out, cache_read


def _tool_input_from_context(context: Any) -> dict[str, Any]:
    """When Agents SDK calls `on_tool_start` for a function tool, the
    `context` is a `ToolContext` carrying `tool_arguments` (JSON string).
    Parse and return as dict."""
    args = getattr(context, "tool_arguments", None)
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except Exception:
            return {"_raw": args}
    return {}


def _tool_call_id(context: Any) -> str | None:
    return getattr(context, "tool_call_id", None)


# ---------- emitter ---------------------------------------------------------


class _AgentsEmitter:
    """Holds session state and emits SAFER events for one
    `SaferRunHooks` instance.  The hooks themselves are stateless — all
    state lives here so multiple `Runner.run()` calls with the same
    hooks produce distinct sessions (one per run)."""

    def __init__(
        self,
        *,
        agent_id: str,
        agent_name: str | None,
        session_id: str | None,
        safer_client: SaferClient | None,
    ) -> None:
        from ._bootstrap import ensure_runtime

        ensure_runtime(agent_id, agent_name)
        self.agent_id = agent_id
        self.agent_name = agent_name or agent_id
        self._initial_session_id = session_id
        self._current_session_id: str | None = None
        self._safer = safer_client
        self._sequence = 0
        self._step_count = 0
        self._session_started = False
        self._session_start_ts: float | None = None
        self._total_cost_usd = 0.0
        # tool_call_id -> (tool_name, started_ts) — populated in on_tool_start,
        # drained in on_tool_end.
        self._pending_tool_calls: dict[str, tuple[str, float]] = {}

    @property
    def session_id(self) -> str:
        if self._current_session_id is None:
            self._current_session_id = (
                self._initial_session_id or f"sess_{uuid.uuid4().hex[:16]}"
            )
        return self._current_session_id

    def _begin_session(self) -> None:
        """Start a fresh session for a new Runner.run() invocation."""
        self._current_session_id = f"sess_{uuid.uuid4().hex[:16]}"
        self._sequence = 0
        self._step_count = 0
        self._session_started = False
        self._session_start_ts = time.monotonic()
        self._total_cost_usd = 0.0
        self._pending_tool_calls.clear()

    def _next_seq(self) -> int:
        client = self._safer or get_client()
        if client is not None:
            try:
                return client.next_sequence(self.session_id)
            except Exception:
                pass
        n = self._sequence
        self._sequence += 1
        return n

    def _emit(self, event: Any) -> None:
        client = self._safer or get_client()
        if client is None:
            return
        try:
            client.emit(event)
        except Exception as e:  # pragma: no cover
            log.debug("openai_agents emit failed: %s", e)

    def _ensure_session_started(self, *, agent: Any = None) -> None:
        if self._session_started:
            return
        self._session_started = True
        self._session_start_ts = self._session_start_ts or time.monotonic()
        context = {}
        if agent is not None:
            context["agent_name"] = _agent_name(agent)
        self._emit(
            OnSessionStartPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                agent_name=self.agent_name,
                context=context,
                source="adapter:openai_agents",
            )
        )

    # ---------- the hook callbacks ----------

    def on_agent_start(self, agent: Any) -> None:
        # First on_agent_start of a run = session start
        if not self._session_started:
            self._begin_session()
            self._ensure_session_started(agent=agent)

    def on_agent_end(self, agent: Any, output: Any) -> None:
        # Final agent in the run produces the user-facing output
        text = _safe_str(output, 4000)
        self._emit(
            OnFinalOutputPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                final_response=text,
                total_steps=self._step_count,
                source="adapter:openai_agents",
            )
        )
        # Close the session
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
                success=True,
                source="adapter:openai_agents",
            )
        )
        # Mark closed so next run rotates the session_id
        self._session_started = False
        self._current_session_id = None

    def on_handoff(self, from_agent: Any, to_agent: Any) -> None:
        self._emit(
            OnAgentDecisionPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                decision_type="handoff",
                chosen_action=_agent_name(to_agent),
                reasoning=f"handoff from {_agent_name(from_agent)}",
                source="adapter:openai_agents",
            )
        )

    def on_llm_start(
        self, agent: Any, system_prompt: str | None, input_items: Any
    ) -> None:
        self._ensure_session_started(agent=agent)
        prompt = _input_items_to_prompt(input_items)
        self._emit(
            BeforeLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                model=_agent_model(agent),
                prompt=prompt,
                tools=_agent_tool_names(agent),
                source="adapter:openai_agents",
            )
        )

    def on_llm_end(self, agent: Any, response: Any) -> None:
        model = _agent_model(agent)
        tokens_in, tokens_out, cache_read = _model_response_usage(response)
        text = _model_response_text(response)
        cost = _estimate_cost(model, tokens_in, tokens_out, cache_read)
        self._total_cost_usd += cost
        self._emit(
            AfterLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                model=model,
                response=text[:8000],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read_tokens=cache_read,
                cost_usd=cost,
                latency_ms=0,  # Agents SDK doesn't expose per-call latency here
                source="adapter:openai_agents",
            )
        )
        self._step_count += 1

    def on_tool_start(self, agent: Any, tool: Any, context: Any = None) -> None:
        tool_name = str(getattr(tool, "name", None) or "tool")
        args = _tool_input_from_context(context) if context is not None else {}
        call_id = _tool_call_id(context) if context is not None else None
        # Always emit on_agent_decision for tool calls — gives Multi-Persona
        # Judge the right context.  Agents SDK doesn't separately fire
        # an explicit decision event so we synthesize from the tool start.
        try:
            args_repr = json.dumps(args)[:1000]
        except Exception:
            args_repr = _safe_str(args, 1000)
        self._emit(
            OnAgentDecisionPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                decision_type="tool_call",
                chosen_action=f"{tool_name}({args_repr})",
                source="adapter:openai_agents",
            )
        )
        self._emit(
            BeforeToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                tool_name=tool_name,
                args=args if isinstance(args, dict) else {"value": args},
                source="adapter:openai_agents",
            )
        )
        if call_id:
            self._pending_tool_calls[call_id] = (tool_name, time.monotonic())

    def on_tool_end(self, agent: Any, tool: Any, result: Any, context: Any = None) -> None:
        tool_name = str(getattr(tool, "name", None) or "tool")
        call_id = _tool_call_id(context) if context is not None else None
        started_ts = (
            self._pending_tool_calls.pop(call_id, (tool_name, time.monotonic()))[1]
            if call_id and call_id in self._pending_tool_calls
            else time.monotonic()
        )
        duration_ms = int((time.monotonic() - started_ts) * 1000)
        self._emit(
            AfterToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                tool_name=tool_name,
                result=_safe_str(result, 4000),
                duration_ms=duration_ms,
                source="adapter:openai_agents",
            )
        )
        self._step_count += 1


# ---------- public hook classes ---------------------------------------------


def _build_run_hooks_class() -> type:
    """Build the SaferRunHooks class lazily — Agents SDK is an optional dep."""
    try:
        from agents import RunHooks
    except ImportError as e:
        raise ImportError(
            "SaferRunHooks requires `openai-agents`. "
            "Install with `pip install openai-agents` or "
            "`pip install 'safer-sdk[openai-agents]'`."
        ) from e

    class _SaferRunHooks(RunHooks):  # type: ignore[misc, valid-type]
        """`RunHooks` implementation that emits SAFER events.

        Pass an instance to `Runner.run(agent, input, hooks=...)`.  A single
        instance can be reused across multiple runs — each new run rotates
        to a fresh SAFER session_id so the dashboard shows distinct rows."""

        def __init__(
            self,
            *,
            agent_id: str,
            agent_name: str | None = None,
            session_id: str | None = None,
            client: SaferClient | None = None,
        ) -> None:
            super().__init__()
            self._emitter = _AgentsEmitter(
                agent_id=agent_id,
                agent_name=agent_name,
                session_id=session_id,
                safer_client=client,
            )

        @property
        def session_id(self) -> str:
            return self._emitter.session_id

        async def on_agent_start(self, context, agent) -> None:  # type: ignore[override]
            try:
                self._emitter.on_agent_start(agent)
            except Exception as e:  # pragma: no cover
                log.debug("on_agent_start emit failed: %s", e)

        async def on_agent_end(self, context, agent, output) -> None:  # type: ignore[override]
            try:
                self._emitter.on_agent_end(agent, output)
            except Exception as e:  # pragma: no cover
                log.debug("on_agent_end emit failed: %s", e)

        async def on_handoff(self, context, from_agent, to_agent) -> None:  # type: ignore[override]
            try:
                self._emitter.on_handoff(from_agent, to_agent)
            except Exception as e:  # pragma: no cover
                log.debug("on_handoff emit failed: %s", e)

        async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:  # type: ignore[override]
            try:
                self._emitter.on_llm_start(agent, system_prompt, input_items)
            except Exception as e:  # pragma: no cover
                log.debug("on_llm_start emit failed: %s", e)

        async def on_llm_end(self, context, agent, response) -> None:  # type: ignore[override]
            try:
                self._emitter.on_llm_end(agent, response)
            except Exception as e:  # pragma: no cover
                log.debug("on_llm_end emit failed: %s", e)

        async def on_tool_start(self, context, agent, tool) -> None:  # type: ignore[override]
            try:
                self._emitter.on_tool_start(agent, tool, context=context)
            except Exception as e:  # pragma: no cover
                log.debug("on_tool_start emit failed: %s", e)

        async def on_tool_end(self, context, agent, tool, result) -> None:  # type: ignore[override]
            try:
                self._emitter.on_tool_end(agent, tool, result, context=context)
            except Exception as e:  # pragma: no cover
                log.debug("on_tool_end emit failed: %s", e)

    return _SaferRunHooks


def _build_tracing_processor_class() -> type:
    """Build the SaferTracingProcessor class lazily."""
    try:
        from agents.tracing import TracingProcessor
    except ImportError as e:
        raise ImportError(
            "SaferTracingProcessor requires `openai-agents`. "
            "Install with `pip install openai-agents`."
        ) from e

    class _SaferTracingProcessor(TracingProcessor):  # type: ignore[misc, valid-type]
        """`TracingProcessor` that emits SAFER events from Agents SDK spans.

        Complements `SaferRunHooks` by surfacing span-level data (e.g.
        guardrail trips, MCP server calls, OpenAI server-side tool spans
        like web_search / file_search / code_interpreter) that the
        per-call hooks don't reach."""

        def __init__(
            self,
            *,
            agent_id: str,
            agent_name: str | None = None,
            client: SaferClient | None = None,
        ) -> None:
            self._agent_id = agent_id
            self._agent_name = agent_name or agent_id
            self._client = client

        def _get_client(self) -> SaferClient | None:
            return self._client or get_client()

        def _emit(self, event: Any) -> None:
            client = self._get_client()
            if client is None:
                return
            try:
                client.emit(event)
            except Exception as e:  # pragma: no cover
                log.debug("openai_agents trace processor emit failed: %s", e)

        def on_trace_start(self, trace) -> None:
            # Trace start is observation-only here — `RunHooks.on_agent_start`
            # already opens a SAFER session for the run.
            pass

        def on_trace_end(self, trace) -> None:
            pass

        def on_span_start(self, span) -> None:
            # Span start is observation-only too; we emit on span_end when
            # we have full data.
            pass

        def on_span_end(self, span) -> None:
            try:
                self._maybe_emit_span(span)
            except Exception as e:  # pragma: no cover
                log.debug("span emit failed: %s", e)

        def _maybe_emit_span(self, span: Any) -> None:
            data = getattr(span, "span_data", None)
            if data is None:
                return
            cls_name = type(data).__name__
            error = getattr(span, "error", None) or getattr(data, "error", None)
            if error:
                # Surface guardrail trips, MCP errors, etc.  RunHooks doesn't
                # expose these so the trace processor is the only signal.
                self._emit(
                    OnErrorPayload(
                        session_id=getattr(span, "trace_id", "otel_span")[:32]
                        or "trace_unknown",
                        agent_id=self._agent_id,
                        sequence=0,  # span ordering is provided by the SDK; no SAFER counter needed
                        error_type=f"{cls_name}_error",
                        message=str(error)[:2000],
                        source="adapter:openai_agents:trace",
                    )
                )

        def force_flush(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

    return _SaferTracingProcessor


_CACHED_HOOKS_CLASS: type | None = None
_CACHED_PROCESSOR_CLASS: type | None = None


def _get_hooks_class() -> type:
    global _CACHED_HOOKS_CLASS
    if _CACHED_HOOKS_CLASS is None:
        _CACHED_HOOKS_CLASS = _build_run_hooks_class()
    return _CACHED_HOOKS_CLASS


def _get_processor_class() -> type:
    global _CACHED_PROCESSOR_CLASS
    if _CACHED_PROCESSOR_CLASS is None:
        _CACHED_PROCESSOR_CLASS = _build_tracing_processor_class()
    return _CACHED_PROCESSOR_CLASS


class SaferRunHooks:
    """`RunHooks` implementation for the OpenAI Agents SDK.

    Pass to `Runner.run(agent, input, hooks=SaferRunHooks(agent_id="..."))`.
    A single instance can be reused across runs — each new run rotates
    to a fresh SAFER session_id."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        real = _get_hooks_class()
        return real(*args, **kwargs)


class SaferTracingProcessor:
    """`TracingProcessor` for the OpenAI Agents SDK.

    Register globally via `agents.tracing.add_trace_processor(...)`."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        real = _get_processor_class()
        return real(*args, **kwargs)


_REGISTERED_PROCESSORS: set[str] = set()


def install_safer_for_agents(
    *,
    agent_id: str,
    agent_name: str | None = None,
) -> Any:
    """One-call setup for SAFER + OpenAI Agents SDK.

    1. Registers a `SaferTracingProcessor` globally (idempotent — calling
       twice for the same agent_id is a no-op).
    2. Returns a `SaferRunHooks` instance to pass per-run.

    Usage:

        from agents import Runner
        from safer.adapters.openai_agents import install_safer_for_agents

        hooks = install_safer_for_agents(agent_id="my_agent")
        result = await Runner.run(agent, "hello", hooks=hooks)
    """
    if agent_id not in _REGISTERED_PROCESSORS:
        try:
            from agents.tracing import add_trace_processor
        except ImportError as e:
            raise ImportError(
                "install_safer_for_agents requires `openai-agents`."
            ) from e
        add_trace_processor(SaferTracingProcessor(agent_id=agent_id, agent_name=agent_name))
        _REGISTERED_PROCESSORS.add(agent_id)
    return SaferRunHooks(agent_id=agent_id, agent_name=agent_name)


def _reset_for_tests() -> None:
    global _CACHED_HOOKS_CLASS, _CACHED_PROCESSOR_CLASS
    _CACHED_HOOKS_CLASS = None
    _CACHED_PROCESSOR_CLASS = None
    _REGISTERED_PROCESSORS.clear()


__all__ = [
    "SaferRunHooks",
    "SaferTracingProcessor",
    "install_safer_for_agents",
    "wrap_openai",  # backward compat re-export
]
