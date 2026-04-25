"""LangChain adapter — `BaseCallbackHandler` subclass that maps LangChain's
native callbacks onto SAFER's 9-hook lifecycle.

Usage:

    from safer.adapters.langchain import SaferCallbackHandler

    handler = SaferCallbackHandler(agent_id="code_analyst", agent_name="Code Analyst")
    result = agent_executor.invoke({"input": "..."}, config={"callbacks": [handler]})

For asynchronous agents, prefer `AsyncSaferCallbackHandler` — same surface,
async callbacks, no thread switching per event.

LangChain is an optional dependency; both classes raise a clear ImportError
on construction if `langchain-core` is not installed.

Hook mapping (LangChain → SAFER):

  on_chain_start (parent_run_id is None) → on_session_start
  on_llm_start / on_chat_model_start     → before_llm_call
  on_llm_end                             → after_llm_call
  on_tool_start                          → before_tool_use
  on_tool_end                            → after_tool_use
  on_agent_action                        → on_agent_decision
  on_agent_finish                        → on_final_output (+ on_session_end)
  on_chain_end (run_id == root_run_id)   → on_final_output (if not yet) + on_session_end
  on_chain_error (run_id == root_run_id) → on_error + on_session_end
  on_*_error                             → on_error
  on_retriever_start / on_retriever_end  → before/after_tool_use (RAG-as-tool)

Session boundary detection — `parent_run_id is None` on `on_chain_start`
identifies the OUTERMOST runnable in the current invocation.  We capture
that run_id as `_root_run_id`; the matching `on_chain_end` /
`on_chain_error` closes the SAFER session.  This makes the adapter work
correctly for `AgentExecutor`, plain LCEL pipelines, and LangGraph nodes
— in earlier versions only `AgentExecutor` (which fires `on_agent_finish`)
ever closed the SAFER session.
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

log = logging.getLogger("safer.adapters.langchain")


# LangChain's BaseCallbackHandler / AsyncCallbackHandler are imported lazily
# so `safer.adapters` stays import-safe when `langchain_core` isn't installed.
def _import_base() -> tuple[type, type]:
    try:
        from langchain_core.callbacks import (
            AsyncCallbackHandler,
            BaseCallbackHandler,
        )

        return BaseCallbackHandler, AsyncCallbackHandler
    except ImportError:
        try:
            from langchain.callbacks.base import (  # type: ignore
                AsyncCallbackHandler,
                BaseCallbackHandler,
            )

            return BaseCallbackHandler, AsyncCallbackHandler
        except ImportError as e:
            raise ImportError(
                "SaferCallbackHandler requires `langchain-core`. "
                "Install it with `pip install langchain-core` "
                "(or `safer-sdk[langchain]`)."
            ) from e


# ---------- pricing — delegated to safer._pricing ---------------------------


def _estimate_cost(
    model: str, tokens_in: int, tokens_out: int, cache_read: int = 0
) -> float:
    """Estimate USD cost via the shared pricing table; 0.0 for unknown models.

    Critical: previous versions silently fell back to Opus pricing when the
    model was unknown.  That overstated Sonnet/Haiku/non-Anthropic costs.
    Now we return 0.0 for unknown models — better to under-report than to
    invent fake numbers."""
    from .._pricing import estimate_cost

    cost = estimate_cost(
        model, tokens_in=tokens_in, tokens_out=tokens_out, cache_read=cache_read
    )
    return cost or 0.0


# ---------- helpers ---------------------------------------------------------


def _safe_str(obj: Any, limit: int = 4000) -> str:
    if obj is None:
        return ""
    s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


def _content_to_text(content: Any) -> str:
    """Flatten a `BaseMessage.content` value into plain text.

    LangChain message content can be one of:
      * a string (legacy, OpenAI-style)
      * a list of content blocks (Anthropic-style: [{"type": "text", "text": "..."},
        {"type": "image", ...}, {"type": "tool_use", ...}])
      * a Pydantic block object with a `.text` attr (older Anthropic adapter)

    Older versions of this adapter did `str(content)` on lists, which produced
    `"[{'type': 'text', 'text': 'Hello'}, ...]"` strings — readable for
    humans, useless for the Judge.  This helper extracts ONLY text content;
    image / tool_use / tool_result blocks are skipped (they are surfaced via
    other SAFER hooks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            else:
                btype = getattr(block, "type", None)
                if btype == "text":
                    parts.append(str(getattr(block, "text", "") or ""))
        return "".join(parts)
    return str(content)


def _messages_to_prompt(messages: list[list[Any]] | list[Any]) -> tuple[str, str | None]:
    """Convert `on_chat_model_start`'s `messages` parameter to a prompt
    preview + the system prompt (if any).

    `messages` is either `list[list[BaseMessage]]` (one batch entry per call)
    or a flat `list[BaseMessage]` (some callers).  We normalize to a flat
    list, then label each entry with its role (so the Judge sees `[user] ...`
    rather than a homogenized blob)."""
    flat: list[Any] = []
    if not messages:
        return "", None
    if isinstance(messages[0], list):
        for batch in messages:
            flat.extend(batch or [])
    else:
        flat = list(messages)

    parts: list[str] = []
    system_text: str | None = None
    for m in flat:
        text = _content_to_text(getattr(m, "content", None) or getattr(m, "text", None))
        role = getattr(m, "type", None) or getattr(m, "role", None) or "user"
        if role == "system":
            # Capture system prompt for profile sync; do not include in
            # prompt preview (system text is usually huge boilerplate).
            if system_text is None and text:
                system_text = text
        elif text:
            parts.append(f"[{role}] {text}")
    return "\n".join(parts), system_text


def _extract_model(
    serialized: Any, llm_output: Any, response: Any = None, kwargs: dict | None = None
) -> str:
    """Resolve the model name from any of the places LangChain might put it.

    Provider-specific pitfalls:
      * `langchain_anthropic.ChatAnthropic` populates `llm_output["model"]`
        (singular) — older code looked for `model_name` and missed it,
        causing every Anthropic call to be billed at Opus prices via the
        unknown-model fallback.
      * `langchain_openai.ChatOpenAI` populates `llm_output["model_name"]`.
      * The `invocation_params` kwarg on the start event has the request's
        actual model.
      * Newer LangChain stores the response model on
        `response.generations[0][0].message.response_metadata["model"]`.
    """
    # 1. response_metadata on the first generation (most authoritative)
    if response is not None:
        try:
            generations = getattr(response, "generations", None) or []
            if generations and generations[0]:
                first = generations[0][0]
                msg = getattr(first, "message", None)
                if msg is not None:
                    rm = getattr(msg, "response_metadata", None)
                    if isinstance(rm, dict):
                        m = rm.get("model") or rm.get("model_name")
                        if isinstance(m, str) and m:
                            return m
        except Exception:
            pass
    # 2. llm_output (Anthropic uses "model", OpenAI uses "model_name")
    if isinstance(llm_output, dict):
        for key in ("model", "model_name", "name"):
            v = llm_output.get(key)
            if isinstance(v, str) and v:
                return v
    # 3. invocation_params kwarg
    if isinstance(kwargs, dict):
        ip = kwargs.get("invocation_params") or {}
        if isinstance(ip, dict):
            for key in ("model", "model_name"):
                v = ip.get(key)
                if isinstance(v, str) and v:
                    return v
    # 4. serialized["kwargs"]
    if isinstance(serialized, dict):
        ser_kwargs = serialized.get("kwargs") or {}
        if isinstance(ser_kwargs, dict):
            for key in ("model", "model_name"):
                v = ser_kwargs.get(key)
                if isinstance(v, str) and v:
                    return v
    return "unknown"


def _extract_tools(serialized: Any, kwargs: dict | None) -> list[dict[str, Any]]:
    """Pull the tool list passed to the model.  LangChain stores it in
    `kwargs["invocation_params"]["tools"]` (post-`bind_tools`), or in
    `serialized["kwargs"]["tools"]` for some chat-model wrappers.

    Output is normalized to `[{"name": str, "description": str | None}]`
    so SAFER's Judge sees a uniform shape regardless of provider."""
    raw: list[Any] | None = None
    if isinstance(kwargs, dict):
        ip = kwargs.get("invocation_params") or {}
        if isinstance(ip, dict):
            raw = ip.get("tools")
    if raw is None and isinstance(serialized, dict):
        ser_kwargs = serialized.get("kwargs") or {}
        if isinstance(ser_kwargs, dict):
            raw = ser_kwargs.get("tools")
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for t in raw:
        if isinstance(t, dict):
            # Anthropic shape: {"name": ..., "description": ..., "input_schema": ...}
            name = t.get("name")
            desc = t.get("description")
            # OpenAI shape: {"type": "function", "function": {"name": ..., ...}}
            if not name and isinstance(t.get("function"), dict):
                fn = t["function"]
                name = fn.get("name")
                desc = fn.get("description")
            if name:
                out.append({"name": str(name), "description": _safe_str(desc, 200) or None})
        else:
            n = getattr(t, "name", None) or getattr(t, "__name__", None)
            if n:
                out.append({"name": str(n), "description": None})
    return out


def _extract_tokens(llm_output: Any) -> tuple[int, int, int]:
    """Pull (tokens_in, tokens_out, cache_read) from a LangChain response."""
    if not isinstance(llm_output, dict):
        return 0, 0, 0
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return 0, 0, 0
    tokens_in = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    tokens_out = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    cache_read = int(
        usage.get("cache_read_input_tokens")
        or usage.get("cache_read")
        or 0
    )
    # Anthropic also emits a nested object on the response; check for it.
    cache_creation = usage.get("cache_creation_input_tokens") or 0
    if cache_creation and not cache_read:
        # Treat as input on the cache-write side; not strictly cache_read,
        # but useful as a signal in the dashboard.
        pass
    return tokens_in, tokens_out, cache_read


def _extract_response_text(response: Any) -> str:
    """Extract assistant text from a LangChain LLMResult."""
    try:
        generations = getattr(response, "generations", []) or []
        if generations and generations[0]:
            first = generations[0][0]
            text = getattr(first, "text", "") or ""
            if not text:
                msg = getattr(first, "message", None)
                if msg is not None:
                    return _content_to_text(getattr(msg, "content", None))
            return text
    except Exception:
        pass
    return ""


def _extract_tool_output(output: Any) -> str:
    """LangChain `on_tool_end` `output` can be a string or a `(content, artifact)`
    tuple when the tool sets `response_format="content_and_artifact"`."""
    if isinstance(output, tuple) and len(output) == 2:
        return _safe_str(output[0])
    return _safe_str(output)


def _extract_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Pull `tool_calls` from a LangChain `LLMResult`'s top AIMessage.

    Modern `create_agent` (LangGraph) emits tool_calls as an attribute
    of the AIMessage instead of firing `on_agent_action`, so the
    LangChain SAFER handler synthesizes `on_agent_decision` events from
    this list. Each entry is a dict like
    `{"id": "tc_...", "name": "...", "args": {...}}`.
    """
    try:
        generations = getattr(response, "generations", []) or []
        if not generations or not generations[0]:
            return []
        msg = getattr(generations[0][0], "message", None)
        if msg is None:
            return []
        tcs = getattr(msg, "tool_calls", None) or []
        result: list[dict[str, Any]] = []
        for tc in tcs:
            if isinstance(tc, dict):
                result.append(tc)
            else:
                result.append(
                    {
                        "id": getattr(tc, "id", None),
                        "name": getattr(tc, "name", None),
                        "args": getattr(tc, "args", None) or {},
                    }
                )
        return result
    except Exception:
        return []


# ---------- handler factory -------------------------------------------------
#
# The real classes are built once on first instantiation and cached.  This
# keeps `from safer.adapters.langchain import SaferCallbackHandler` cheap
# (langchain-core import deferred) without rebuilding the class on every
# constructor call.


_CACHED_CLASSES: tuple[type, type] | None = None


def _get_handler_classes() -> tuple[type, type]:
    """Build (sync_class, async_class) once and cache them."""
    global _CACHED_CLASSES
    if _CACHED_CLASSES is not None:
        return _CACHED_CLASSES

    BaseHandler, AsyncBase = _import_base()

    sync_cls = _build_sync_handler(BaseHandler)
    async_cls = _build_async_handler(AsyncBase)

    _CACHED_CLASSES = (sync_cls, async_cls)
    return _CACHED_CLASSES


def _reset_for_tests() -> None:
    """Test hook — discard the cached classes so a fresh import shape is
    rebuilt on next instantiation."""
    global _CACHED_CLASSES
    _CACHED_CLASSES = None


def _build_sync_handler(BaseHandler: type) -> type:
    """Construct the sync `SaferCallbackHandler` class bound to LangChain's
    `BaseCallbackHandler`.  Logic lives in `_HandlerMixin`."""

    class _SyncSaferCallbackHandler(_HandlerMixin, BaseHandler):  # type: ignore[misc, valid-type]
        """Synchronous LangChain callback handler that emits SAFER events."""

    return _SyncSaferCallbackHandler


def _build_async_handler(AsyncBase: type) -> type:
    """Construct the async `AsyncSaferCallbackHandler` class.

    LangChain dispatches sync handlers in a thread pool; for native async
    agents this costs a context switch per event.  An `AsyncCallbackHandler`
    subclass with `async def` callbacks runs in the same event loop.  All
    SAFER emit logic is sync (the transport is fire-and-forget), so the
    async wrappers just delegate to the sync logic — no `await` needed
    inside the body, but the method must be `async def` for LangChain to
    call it via the async dispatch path."""

    base_methods = {
        "on_llm_start", "on_chat_model_start", "on_llm_end", "on_llm_error",
        "on_chain_start", "on_chain_end", "on_chain_error",
        "on_tool_start", "on_tool_end", "on_tool_error",
        "on_agent_action", "on_agent_finish",
        "on_retriever_start", "on_retriever_end", "on_retriever_error",
    }

    def _make_async_method(name: str):
        async def _async_method(self, *args, **kwargs):
            # Look up the sync impl on the mixin and call it
            sync_impl = getattr(_HandlerMixin, name)
            return sync_impl(self, *args, **kwargs)

        _async_method.__name__ = name
        return _async_method

    namespace = {name: _make_async_method(name) for name in base_methods}

    return type(
        "_AsyncSaferCallbackHandler",
        (_HandlerMixin, AsyncBase),
        namespace,
    )


# ---------- shared handler logic --------------------------------------------


class _HandlerMixin:
    """Implements the LangChain → SAFER mapping.

    Mixed into both sync and async handler classes; every method is sync
    (transport is fire-and-forget).  The async subclass wraps each method in
    an `async def` so LangChain's async dispatch path can call them without
    a thread switch."""

    raise_error = False
    ignore_llm = False
    ignore_chain = False
    ignore_agent = False
    ignore_retriever = False  # we map retriever events to tool_use
    ignore_chat_model = False

    def __init__(
        self,
        *,
        agent_id: str,
        agent_name: str | None = None,
        session_id: str | None = None,
        client: SaferClient | None = None,
        pin_session: bool = False,
    ) -> None:
        from ._bootstrap import ensure_runtime

        ensure_runtime(agent_id, agent_name)
        # NB: BaseCallbackHandler.__init__ takes no args, so `super().__init__()`
        # is a no-op; the subclass that mixes us in calls it via the type
        # MRO automatically when it's instantiated.  We don't call super here.
        self.agent_id = agent_id
        self.agent_name = agent_name or agent_id
        # `_initial_session_id` pins the FIRST root-chain only.  Subsequent
        # invocations rotate through fresh ids — see `_begin_session`.
        # `pin_session=True` flips that: every invocation reuses the same
        # session_id and `on_session_end` is deferred to atexit so a chat
        # REPL becomes one logical SAFER session.
        self._initial_session_id = session_id
        self._current_session_id: str | None = None
        self._client = client
        self._pin_session = pin_session
        # Per-invocation state — reset by _begin_session.
        self._session_started = False
        self._sequence = 0
        self._step_count = 0
        self._total_cost_usd = 0.0
        self._session_start_ts: float | None = None
        self._final_emitted = False
        self._root_run_id: str | None = None
        self._llm_start_ts: dict[str, float] = {}
        self._tool_start_ts: dict[str, float] = {}
        self._tool_name_by_rid: dict[str, str] = {}
        self._llm_model_by_rid: dict[str, str] = {}
        self._seen_tool_call_ids: set[str] = set()
        self._profile_synced = False
        self._atexit_registered = False
        if pin_session:
            self._register_atexit_close()

    # ---------- internal helpers ----------

    @property
    def session_id(self) -> str:
        if self._current_session_id is None:
            self._current_session_id = (
                self._initial_session_id or f"sess_{uuid.uuid4().hex[:16]}"
            )
        return self._current_session_id

    def _begin_session(self) -> None:
        """Start a fresh SAFER session for a new root-chain invocation.

        With `pin_session=True` we keep the session_id stable across
        invocations and only zero the per-invocation working maps —
        sequence + step count keep growing for accurate session-wide
        accounting.
        """
        if self._pin_session:
            self._llm_start_ts.clear()
            self._tool_start_ts.clear()
            self._tool_name_by_rid.clear()
            self._llm_model_by_rid.clear()
            self._seen_tool_call_ids.clear()
            return
        self._current_session_id = f"sess_{uuid.uuid4().hex[:16]}"
        self._session_started = False
        self._sequence = 0
        self._step_count = 0
        self._total_cost_usd = 0.0
        self._session_start_ts = time.monotonic()
        self._final_emitted = False
        self._llm_start_ts.clear()
        self._tool_start_ts.clear()
        self._tool_name_by_rid.clear()
        self._llm_model_by_rid.clear()
        self._seen_tool_call_ids.clear()

    def _register_atexit_close(self) -> None:
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
            self._emit_session_end(success=True)
        except Exception:  # pragma: no cover — atexit must never raise
            pass

    def close_session(self, *, success: bool = True) -> None:
        """Manually close the pinned chat session. No-op when
        pin_session is False (per-invocation lifecycle handles it)."""
        if not self._pin_session or not self._session_started:
            return
        self._emit_session_end(success=success)

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
        except Exception as e:  # pragma: no cover — transport errors
            log.debug("langchain adapter emit failed: %s", e)

    def _emit_error(self, err: BaseException) -> None:
        try:
            self._emit(
                OnErrorPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    error_type=type(err).__name__,
                    message=_safe_str(err, 2000),
                )
            )
        except Exception:  # pragma: no cover
            pass

    def _ensure_session_started(self, context: dict[str, Any] | None = None) -> None:
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

    def _emit_session_end(self, success: bool = True) -> None:
        duration_ms = 0
        if self._session_start_ts is not None:
            duration_ms = int((time.monotonic() - self._session_start_ts) * 1000)
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
        # Mark closed so a subsequent root-chain rotates to a fresh session_id.
        self._session_started = False
        self._root_run_id = None

    def _maybe_close_invocation(self, *, success: bool = True) -> None:
        """Close out a root-chain invocation.

        With `pin_session=False` (default) this emits `on_session_end`
        and marks the session closed so the next root-chain gets a fresh
        session_id.

        With `pin_session=True` we only release the root_run_id binding
        so the next root-chain can be tracked, but we keep the SAFER
        session open — the atexit hook (or a manual `close_session()`)
        emits the single closing `on_session_end`.
        """
        if self._pin_session:
            self._root_run_id = None
            return
        self._emit_session_end(success=success)
        self._final_emitted = False

    def _maybe_sync_profile(self, system_text: str | None) -> None:
        if self._profile_synced or not system_text:
            return
        client = self._get_client()
        if client is None:
            return
        self._profile_synced = True
        try:
            client.schedule_profile_patch(
                self.agent_id,
                system_prompt=system_text.strip() or None,
                name=self.agent_name,
            )
        except Exception as e:  # pragma: no cover
            log.debug("langchain profile sync failed: %s", e)

    # ---------- chain lifecycle ----------

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: Any | None = None,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        if parent_run_id is None:
            # Outermost runnable in this invocation — start a fresh SAFER session
            self._begin_session()
            self._root_run_id = rid
            self._ensure_session_started(
                context={"inputs": _safe_preview(inputs)}
            )

    def on_chain_end(
        self, outputs: Any, *, run_id: Any | None = None, **kwargs: Any
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        if not self._root_run_id or rid != self._root_run_id:
            # Sub-chain end — ignore.
            return
        # Root chain completed.  Emit on_final_output if `on_agent_finish`
        # didn't already do so, then close the session.
        if not self._final_emitted:
            text = _extract_output_text(outputs)
            self._emit(
                OnFinalOutputPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_sequence(),
                    final_response=_safe_str(text, 4000),
                    total_steps=self._step_count,
                )
            )
            self._final_emitted = True
        self._maybe_close_invocation(success=True)

    def on_chain_error(
        self, error: BaseException, *, run_id: Any | None = None, **kwargs: Any
    ) -> None:
        self._emit_error(error)
        rid = str(run_id) if run_id is not None else ""
        if self._root_run_id and rid == self._root_run_id:
            # Root chain errored — close the invocation.
            self._maybe_close_invocation(success=False)

    # ---------- llm lifecycle ----------

    def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: list[str],
        *,
        run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        # Some pipelines fire on_llm_start without a preceding on_chain_start
        # (e.g. when the user invokes a raw chat model).  Start a session if
        # we don't have one yet so events still flow.
        if not self._session_started:
            self._begin_session()
            self._ensure_session_started()

        rid = str(run_id) if run_id is not None else f"llm_{uuid.uuid4().hex[:8]}"
        self._llm_start_ts[rid] = time.monotonic()
        model = _extract_model(serialized, None, response=None, kwargs=kwargs)
        self._llm_model_by_rid[rid] = model
        prompt = "\n\n".join(prompts) if prompts else ""
        tools = _extract_tools(serialized, kwargs)
        invocation_params = kwargs.get("invocation_params") or {}
        self._emit(
            BeforeLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_sequence(),
                model=model,
                prompt=_safe_str(prompt, 8000),
                tools=tools,
                temperature=invocation_params.get("temperature")
                if isinstance(invocation_params, dict)
                else None,
                max_tokens=invocation_params.get("max_tokens")
                if isinstance(invocation_params, dict)
                else None,
            )
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: list[list[Any]],
        *,
        run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        # Walk content blocks instead of stringifying — the Judge needs real
        # text, not a Pydantic repr.
        prompt, system_text = _messages_to_prompt(messages)
        self._maybe_sync_profile(system_text)
        # Delegate to on_llm_start with the assembled prompt
        self.on_llm_start(
            serialized,
            [prompt] if prompt else [""],
            run_id=run_id,
            **kwargs,
        )

    def on_llm_end(
        self, response: Any, *, run_id: Any | None = None, **kwargs: Any
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        started = self._llm_start_ts.pop(rid, None)
        latency_ms = int((time.monotonic() - started) * 1000) if started else 0
        # Resolve model name from start cache OR from response metadata
        model = self._llm_model_by_rid.pop(rid, None) or _extract_model(
            None, getattr(response, "llm_output", None), response=response, kwargs=kwargs
        )
        if model == "unknown":
            # Try response metadata one more time
            re_model = _extract_model(None, getattr(response, "llm_output", None), response=response)
            if re_model != "unknown":
                model = re_model

        llm_output = getattr(response, "llm_output", None)
        tokens_in, tokens_out, cache_read = _extract_tokens(llm_output)
        text = _extract_response_text(response)
        cost = _estimate_cost(model, tokens_in, tokens_out, cache_read)
        self._total_cost_usd += cost
        self._emit(
            AfterLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_sequence(),
                model=model,
                response=_safe_str(text, 8000),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read_tokens=cache_read,
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        )
        self._step_count += 1
        # Synthesize on_agent_decision from any tool_calls embedded in the
        # AIMessage. Modern LangChain (`create_agent`, LangGraph) does not
        # fire `on_agent_action` — tool calls land here as message
        # metadata, so we have to surface them ourselves. The
        # `_seen_tool_call_ids` set deduplicates against legacy
        # `on_agent_action` emissions in old AgentExecutor pipelines.
        for tc in _extract_tool_calls(response):
            tc_id = str(tc.get("id") or "")
            if tc_id and tc_id in self._seen_tool_call_ids:
                continue
            if tc_id:
                self._seen_tool_call_ids.add(tc_id)
            self._emit_decision_from_tool_call(tc)

    def _emit_decision_from_tool_call(self, tc: dict[str, Any]) -> None:
        name = str(tc.get("name") or "tool")
        args = tc.get("args") or {}
        try:
            import json as _json

            args_repr = _json.dumps(args)[:1000]
        except Exception:
            args_repr = _safe_str(args, 1000)
        self._emit(
            OnAgentDecisionPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_sequence(),
                decision_type="tool_call",
                chosen_action=f"{name}({args_repr})" if args_repr else name,
                reasoning=None,
            )
        )

    def on_llm_error(
        self, error: BaseException, *, run_id: Any | None = None, **kwargs: Any
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        self._llm_start_ts.pop(rid, None)
        self._llm_model_by_rid.pop(rid, None)
        self._emit_error(error)

    # ---------- tools ----------

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: Any | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        rid = str(run_id) if run_id is not None else f"tool_{uuid.uuid4().hex[:8]}"
        self._tool_start_ts[rid] = time.monotonic()
        tool_name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        # Cache the name so on_tool_end can recover it (kwargs at end is empty)
        self._tool_name_by_rid[rid] = str(tool_name)
        # Prefer the structured `inputs` dict when LangChain passes it;
        # fall back to the stringified input otherwise.
        args: dict[str, Any]
        if isinstance(inputs, dict):
            args = inputs
        else:
            args = {"input": _safe_str(input_str, 2000)}
        self._emit(
            BeforeToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_sequence(),
                tool_name=str(tool_name),
                args=args,
            )
        )

    def on_tool_end(
        self, output: Any, *, run_id: Any | None = None, **kwargs: Any
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        started = self._tool_start_ts.pop(rid, None)
        duration_ms = int((time.monotonic() - started) * 1000) if started else 0
        # Recover the name we stored at start; kwargs.get("name") is unreliable.
        tool_name = self._tool_name_by_rid.pop(rid, None) or kwargs.get("name") or "tool"
        result_text = _extract_tool_output(output)
        self._emit(
            AfterToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_sequence(),
                tool_name=str(tool_name),
                result=result_text,
                duration_ms=duration_ms,
            )
        )
        self._step_count += 1

    def on_tool_error(
        self, error: BaseException, *, run_id: Any | None = None, **kwargs: Any
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        self._tool_start_ts.pop(rid, None)
        self._tool_name_by_rid.pop(rid, None)
        self._emit_error(error)

    # ---------- retriever (mapped to tool_use) ----------

    def on_retriever_start(
        self,
        serialized: dict[str, Any] | None,
        query: str,
        *,
        run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        rid = str(run_id) if run_id is not None else f"ret_{uuid.uuid4().hex[:8]}"
        self._tool_start_ts[rid] = time.monotonic()
        ret_name = (serialized or {}).get("name") or "retriever"
        self._tool_name_by_rid[rid] = str(ret_name)
        self._emit(
            BeforeToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_sequence(),
                tool_name=str(ret_name),
                args={"query": _safe_str(query, 2000)},
            )
        )

    def on_retriever_end(
        self, documents: Any, *, run_id: Any | None = None, **kwargs: Any
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        started = self._tool_start_ts.pop(rid, None)
        duration_ms = int((time.monotonic() - started) * 1000) if started else 0
        tool_name = self._tool_name_by_rid.pop(rid, None) or "retriever"
        # Documents are a Sequence[Document]; show the first chunk + count.
        try:
            docs = list(documents) if documents else []
        except Exception:
            docs = []
        preview = _safe_str(
            f"{len(docs)} document(s); first: {getattr(docs[0], 'page_content', '') if docs else ''}",
            2000,
        )
        self._emit(
            AfterToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_sequence(),
                tool_name=str(tool_name),
                result=preview,
                duration_ms=duration_ms,
            )
        )
        self._step_count += 1

    def on_retriever_error(
        self, error: BaseException, *, run_id: Any | None = None, **kwargs: Any
    ) -> None:
        rid = str(run_id) if run_id is not None else ""
        self._tool_start_ts.pop(rid, None)
        self._tool_name_by_rid.pop(rid, None)
        self._emit_error(error)

    # ---------- agent decisions / final ----------

    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        tool = getattr(action, "tool", None)
        tool_input = getattr(action, "tool_input", None)
        # Dedup: modern `create_agent` synthesizes the same decision
        # from `on_llm_end` via the AIMessage.tool_calls list. If a
        # matching tool_call_id was already emitted there, skip.
        tool_call_id = getattr(action, "tool_call_id", None) or ""
        if tool_call_id and tool_call_id in self._seen_tool_call_ids:
            return
        if tool_call_id:
            self._seen_tool_call_ids.add(tool_call_id)
        # Serialize tool_input to a short JSON-ish string for the decision payload
        if isinstance(tool_input, dict):
            try:
                import json

                action_args = json.dumps(tool_input)[:1000]
            except Exception:
                action_args = _safe_str(tool_input, 1000)
        else:
            action_args = _safe_str(tool_input, 1000)
        self._emit(
            OnAgentDecisionPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_sequence(),
                decision_type="tool_call",
                reasoning=_safe_str(getattr(action, "log", None), 2000),
                chosen_action=f"{tool}({action_args})" if action_args else _safe_str(tool),
            )
        )

    def on_agent_finish(self, finish: Any, **kwargs: Any) -> None:
        text = ""
        return_values = getattr(finish, "return_values", None)
        if isinstance(return_values, dict):
            text = _safe_str(return_values.get("output"), 4000)
        if not text:
            text = _safe_str(getattr(finish, "log", None), 4000)
        self._emit(
            OnFinalOutputPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_sequence(),
                final_response=text,
                total_steps=self._step_count,
            )
        )
        self._final_emitted = True
        # NB: we do NOT close the session here.  The trailing on_chain_end
        # for the root AgentExecutor chain runs immediately after; that's
        # where we emit on_session_end.  This avoids a double-close.


# ---------- output extraction for plain LCEL pipelines ----------------------


def _extract_output_text(outputs: Any) -> str:
    """Best-effort extraction of the assistant's output text from arbitrary
    chain `outputs`.  Handles common LCEL shapes."""
    if outputs is None:
        return ""
    # Dict with the usual keys
    if isinstance(outputs, dict):
        for key in ("output", "answer", "result", "text", "content"):
            v = outputs.get(key)
            if isinstance(v, str) and v:
                return v
        # Fall through to dict repr
        return _safe_str(outputs, 4000)
    # Pydantic message?
    if hasattr(outputs, "content"):
        return _content_to_text(getattr(outputs, "content"))
    # Plain string
    if isinstance(outputs, str):
        return outputs
    return _safe_str(outputs, 4000)


def _safe_preview(obj: Any, limit: int = 500) -> dict[str, Any]:
    if isinstance(obj, dict):
        return {k: _safe_str(v, limit) for k, v in list(obj.items())[:10]}
    return {"value": _safe_str(obj, limit)}


# ---------- public proxies (lazy class build, identity-preserving) ----------
#
# Users import `SaferCallbackHandler` / `AsyncSaferCallbackHandler`.  These
# proxies build the real classes on first instantiation (so import is
# safe without langchain installed) and forward `__new__` to construct an
# instance of the real class — which IS a `BaseCallbackHandler` /
# `AsyncCallbackHandler` for LangChain's `isinstance` checks.


class SaferCallbackHandler:  # type: ignore[no-redef]
    """Thin proxy that constructs the real sync handler on first instantiation."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        sync_cls, _ = _get_handler_classes()
        return sync_cls(*args, **kwargs)


class AsyncSaferCallbackHandler:  # type: ignore[no-redef]
    """Thin proxy that constructs the real async handler on first instantiation."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        _, async_cls = _get_handler_classes()
        return async_cls(*args, **kwargs)


__all__ = ["SaferCallbackHandler", "AsyncSaferCallbackHandler"]
