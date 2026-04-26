"""OpenAI raw SDK adapter — full coverage of `chat.completions` + `responses`.

Two integration paths:

**Path 1 — `wrap_openai` (recommended for OpenAI < the Agents SDK).**

    from openai import OpenAI
    from safer.adapters.openai_client import wrap_openai

    client = wrap_openai(OpenAI(), agent_id="support", agent_name="Support")
    response = client.chat.completions.create(model="gpt-4o", ...)

The wrapper proxies attribute access; only the LLM-call methods are
instrumented:

  * `client.chat.completions.create` (sync + async; streaming + non-streaming)
  * `client.chat.completions.parse`  (structured output)
  * `client.responses.create`        (Responses API; sync + async; streaming)
  * `client.chat.completions.with_raw_response.create` (production correlation)
  * `client.chat.completions.with_streaming_response.create`
  * `client.responses.with_raw_response.create`
  * `client.responses.with_streaming_response.create`

Other namespaces (`client.embeddings`, `client.files`, `client.images`,
`client.batches`, `client.fine_tuning`, `client.vector_stores`, ...) pass
through unchanged.  Earlier adapter versions wrapped *every* `.create()`
method, which produced phantom LLM events for embedding / file-upload /
image-generation calls.

**Path 2 — `safer.adapters.openai_agents` for the OpenAI Agents SDK.**

The Agents SDK has its own first-class hook surface (`RunHooks` +
`TracingProcessor`) — that's a separate adapter, not this file.

This module supports both `OpenAI` and `AsyncOpenAI` (detected at wrap
time).  Streaming responses are accumulated chunk-by-chunk so SAFER sees
real usage + tool_call data on `after_llm_call`, instead of garbage
extracted from a still-open `Stream` iterator.
"""

from __future__ import annotations

import atexit
import inspect
import json
import logging
import threading
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

log = logging.getLogger("safer.adapters.openai")


# ---------- pricing — delegated to safer._pricing ---------------------------


def _estimate_cost(
    model: str, tokens_in: int, tokens_out: int, cache_read: int = 0
) -> float:
    """Estimate USD cost via the shared pricing table; 0.0 for unknown models."""
    from .._pricing import estimate_cost

    cost = estimate_cost(
        model, tokens_in=tokens_in, tokens_out=tokens_out, cache_read=cache_read
    )
    return cost or 0.0


# ---------- whitelist of method paths that ARE LLM calls --------------------
#
# A path is the chain of attribute accesses from the client to the method
# that gets called.  Only these paths produce SAFER LLM events; everything
# else (embeddings, files, images, batches, ...) passes through.

_LLM_PATHS: set[tuple[str, ...]] = {
    # chat.completions
    ("chat", "completions", "create"),
    ("chat", "completions", "parse"),
    ("chat", "completions", "with_raw_response", "create"),
    ("chat", "completions", "with_streaming_response", "create"),
    # responses
    ("responses", "create"),
    ("responses", "with_raw_response", "create"),
    ("responses", "with_streaming_response", "create"),
}


def _is_llm_path(path: list[str]) -> bool:
    return tuple(path) in _LLM_PATHS


# ---------- prompt + response extraction helpers ---------------------------


def _safe_str(obj: Any, limit: int = 8000) -> str:
    if obj is None:
        return ""
    s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


def _extract_prompt(kwargs: dict[str, Any]) -> str:
    """Build a prompt preview from chat-completions or responses input."""
    messages = kwargs.get("messages") or kwargs.get("input")
    if isinstance(messages, str):
        return messages[:8000]
    if not messages:
        # responses API also accepts `instructions=` separately
        instr = kwargs.get("instructions")
        return _safe_str(instr) if isinstance(instr, str) else ""
    parts: list[str] = []
    for m in messages:
        if isinstance(m, dict):
            role = m.get("role", "user")
            content = m.get("content")
            text = _content_to_text(content)
            if text:
                parts.append(f"[{role}] {text}")
        else:
            # Pydantic model
            role = getattr(m, "role", "user")
            text = _content_to_text(getattr(m, "content", None))
            if text:
                parts.append(f"[{role}] {text}")
    return "\n".join(parts)[:8000]


def _content_to_text(content: Any) -> str:
    """Walk OpenAI message content (string OR list of typed parts)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                # chat.completions: {"type": "text", "text": "..."}
                # responses input:  {"type": "input_text", "text": "..."}
                # responses output: {"type": "output_text", "text": "..."}
                ctype = c.get("type", "")
                if ctype in ("text", "input_text", "output_text"):
                    parts.append(str(c.get("text", "")))
            else:
                ctype = getattr(c, "type", None)
                t = getattr(c, "text", None)
                if ctype in ("text", "input_text", "output_text") and t:
                    parts.append(str(t))
        return "".join(parts)
    return str(content)


def _extract_text_from_chat_completion(response: Any) -> str:
    """Extract assistant text from a `ChatCompletion` (non-streaming).

    `response.choices[0].message.content` may be `None` when the model only
    emits tool_calls; in that case we surface a tool_calls summary instead
    so the dashboard shows what happened."""
    try:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        msg = getattr(choices[0], "message", None)
        if msg is None:
            return ""
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content:
            return content
        # Tool-only response — summarize the calls
        tool_calls = getattr(msg, "tool_calls", None) or []
        if tool_calls:
            names = []
            for tc in tool_calls:
                fn = getattr(tc, "function", None)
                names.append(getattr(fn, "name", "tool") if fn else "tool")
            return f"[tool_calls: {', '.join(names)}]"
    except Exception:  # pragma: no cover
        pass
    return ""


def _extract_text_from_response(response: Any) -> str:
    """Extract assistant text from a Responses-API `Response`.

    SDK ships `response.output_text` as the canonical aggregator — use it.
    Falls back to walking `response.output[*].content[*]` for older SDK
    versions."""
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text:
        return text
    # Fallback: walk output items
    parts: list[str] = []
    output = getattr(response, "output", None) or []
    for item in output:
        itype = getattr(item, "type", None) or (
            item.get("type") if isinstance(item, dict) else None
        )
        if itype != "message":
            continue
        content = getattr(item, "content", None) or (
            item.get("content") if isinstance(item, dict) else None
        )
        for block in content or []:
            btype = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )
            t = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else None
            )
            if btype == "output_text" and t:
                parts.append(str(t))
    return "\n".join(parts)


def _extract_chat_usage(response: Any) -> tuple[int, int, int]:
    """Pull (tokens_in, tokens_out, cache_read) from a chat.completions response."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if not usage:
        return 0, 0, 0
    tokens_in = int(_attr(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(_attr(usage, "completion_tokens", 0) or 0)
    # cached_tokens nested under prompt_tokens_details
    details = _attr(usage, "prompt_tokens_details", None)
    cache_read = 0
    if details:
        cache_read = int(_attr(details, "cached_tokens", 0) or 0)
    return tokens_in, tokens_out, cache_read


def _extract_responses_usage(response: Any) -> tuple[int, int, int]:
    """Pull (tokens_in, tokens_out, cache_read) from a Responses-API response."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if not usage:
        return 0, 0, 0
    tokens_in = int(_attr(usage, "input_tokens", 0) or 0)
    tokens_out = int(_attr(usage, "output_tokens", 0) or 0)
    details = _attr(usage, "input_tokens_details", None)
    cache_read = 0
    if details:
        cache_read = int(_attr(details, "cached_tokens", 0) or 0)
    return tokens_in, tokens_out, cache_read


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_chat_tool_calls(response: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """Return [(call_id, tool_name, args), ...] for a chat.completions response."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    try:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return out
        msg = getattr(choices[0], "message", None)
        if msg is None:
            return out
        for tc in getattr(msg, "tool_calls", None) or []:
            tc_id = str(getattr(tc, "id", "") or f"tc_{uuid.uuid4().hex[:8]}")
            fn = getattr(tc, "function", None)
            name = str(getattr(fn, "name", "") or "tool") if fn else "tool"
            args_str = getattr(fn, "arguments", "") if fn else ""
            try:
                args = json.loads(args_str) if args_str else {}
            except Exception:
                args = {"_raw": args_str}
            if not isinstance(args, dict):
                args = {"value": args}
            out.append((tc_id, name, args))
    except Exception:  # pragma: no cover
        pass
    return out


def _extract_responses_tool_calls(response: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """Return [(call_id, tool_name, args), ...] for a Responses-API response.

    The Responses API uses flat `function_call` items in `response.output`."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    try:
        for item in getattr(response, "output", None) or []:
            itype = getattr(item, "type", None) or (
                item.get("type") if isinstance(item, dict) else None
            )
            if itype != "function_call":
                continue
            call_id = str(
                _attr(item, "call_id", None) or _attr(item, "id", None) or f"fc_{uuid.uuid4().hex[:8]}"
            )
            name = str(_attr(item, "name", "") or "tool")
            args_str = _attr(item, "arguments", "") or ""
            try:
                args = json.loads(args_str) if args_str else {}
            except Exception:
                args = {"_raw": args_str}
            if not isinstance(args, dict):
                args = {"value": args}
            out.append((call_id, name, args))
    except Exception:  # pragma: no cover
        pass
    return out


def _drain_chat_tool_results(messages: Any, pending: dict) -> list[tuple[str, str, str]]:
    """Scan the next request's `messages` for `role="tool"` entries and
    pair them with pending tool_calls.  Returns [(call_id, tool_name, content), ...]"""
    drained: list[tuple[str, str, str]] = []
    if not isinstance(messages, list):
        return drained
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role != "tool":
            continue
        call_id = (
            m.get("tool_call_id")
            if isinstance(m, dict)
            else getattr(m, "tool_call_id", None)
        )
        if not call_id or call_id not in pending:
            continue
        content = (
            m.get("content")
            if isinstance(m, dict)
            else getattr(m, "content", None)
        )
        result_text = _content_to_text(content) if not isinstance(content, str) else content
        drained.append((call_id, pending[call_id][0], result_text or ""))
    return drained


def _drain_responses_tool_outputs(input_arr: Any, pending: dict) -> list[tuple[str, str, str]]:
    """Scan the Responses-API next request's `input` array for
    `function_call_output` items and pair them with pending tool_calls."""
    drained: list[tuple[str, str, str]] = []
    if not isinstance(input_arr, list):
        return drained
    for item in input_arr:
        itype = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        if itype != "function_call_output":
            continue
        call_id = (
            item.get("call_id") if isinstance(item, dict) else getattr(item, "call_id", None)
        )
        if not call_id or call_id not in pending:
            continue
        out_text = (
            item.get("output") if isinstance(item, dict) else getattr(item, "output", None)
        )
        if not isinstance(out_text, str):
            out_text = _safe_str(out_text)
        drained.append((call_id, pending[call_id][0], out_text or ""))
    return drained


def _normalize_tools(tools: Any) -> list[dict[str, Any]]:
    """Normalize OpenAI `tools=` arg into [{"name", "description"}, ...]."""
    if not isinstance(tools, list):
        return []
    out: list[dict[str, Any]] = []
    for t in tools:
        if isinstance(t, dict):
            # chat.completions: {"type": "function", "function": {"name": ..., "description": ...}}
            if isinstance(t.get("function"), dict):
                fn = t["function"]
                name = fn.get("name")
                desc = fn.get("description")
            else:
                # responses: {"type": "function", "name": ..., "description": ...}
                name = t.get("name")
                desc = t.get("description")
            if name:
                out.append({"name": str(name), "description": _safe_str(desc, 200) or None})
    return out


def _api_kind_for_path(path: list[str]) -> str:
    """Return 'chat' or 'responses' depending on which API the path targets."""
    if "responses" in path:
        return "responses"
    return "chat"


# ---------- emitter ---------------------------------------------------------


class _OpenAIEmitter:
    """Holds session state + emits SAFER events for one OpenAI client wrapper."""

    def __init__(
        self,
        *,
        agent_id: str,
        agent_name: str | None,
        session_id: str | None,
        safer_client: SaferClient | None,
    ) -> None:
        from ._bootstrap import ensure_runtime

        ensure_runtime(agent_id, agent_name, framework="openai")
        self.agent_id = agent_id
        self.agent_name = agent_name or agent_id
        self._initial_session_id = session_id
        self._current_session_id: str | None = None
        self._safer = safer_client
        self._step_count = 0
        self._session_started = False
        self._session_start_ts: float | None = None
        self._total_cost_usd = 0.0
        self._lock = threading.Lock()
        # call_id -> (tool_name, args, started_ts)
        self._pending_tool_calls: dict[str, tuple[str, dict[str, Any], float]] = {}
        # Register atexit to close session on process exit (raw SDK has no
        # built-in session boundary; this is the production-grade default).
        atexit.register(self._atexit_close)
        self._atexit_done = False

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
        with self._lock:
            n = self._step_count
            self._step_count += 1
            return n

    def _emit(self, event: Any) -> None:
        client = self._safer or get_client()
        if client is None:
            return
        try:
            client.emit(event)
        except Exception as e:  # pragma: no cover
            log.debug("openai adapter emit failed: %s", e)

    def _ensure_session(self) -> None:
        with self._lock:
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
                source="adapter:openai",
            )
        )

    def end_session(self, *, success: bool = True) -> None:
        with self._lock:
            if not self._session_started:
                return
            self._session_started = False
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
                source="adapter:openai",
            )
        )
        self._current_session_id = None  # rotate for next session

    def _atexit_close(self) -> None:
        if self._atexit_done:
            return
        self._atexit_done = True
        try:
            self.end_session(success=True)
        except Exception:  # pragma: no cover
            pass

    # ---------- before/after LLM ----------

    def emit_before_llm(self, kwargs: dict[str, Any], path: list[str]) -> None:
        self._ensure_session()
        api_kind = _api_kind_for_path(path)
        # Drain any pending tool results from the user's outgoing messages
        if api_kind == "chat":
            for call_id, tool_name, result_text in _drain_chat_tool_results(
                kwargs.get("messages"), self._pending_tool_calls
            ):
                self._emit_after_tool_use(call_id, tool_name, result_text)
        else:
            for call_id, tool_name, result_text in _drain_responses_tool_outputs(
                kwargs.get("input"), self._pending_tool_calls
            ):
                self._emit_after_tool_use(call_id, tool_name, result_text)

        model = str(kwargs.get("model") or "openai")
        prompt = _extract_prompt(kwargs)
        tools = _normalize_tools(kwargs.get("tools"))
        # Auto-inject stream_options.include_usage when streaming, unless the
        # user explicitly opted out (passing include_usage=False).
        if kwargs.get("stream"):
            so = kwargs.get("stream_options") or {}
            if isinstance(so, dict) and "include_usage" not in so:
                so = dict(so)
                so["include_usage"] = True
                kwargs["stream_options"] = so

        self._emit(
            BeforeLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                model=model,
                prompt=prompt,
                tools=tools,
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens") or kwargs.get("max_output_tokens"),
                source="adapter:openai",
            )
        )

    def emit_after_llm(
        self, kwargs: dict[str, Any], response: Any, latency_ms: int, path: list[str]
    ) -> None:
        api_kind = _api_kind_for_path(path)
        model = str(getattr(response, "model", None) or kwargs.get("model") or "openai")

        if api_kind == "chat":
            tokens_in, tokens_out, cache_read = _extract_chat_usage(response)
            text = _extract_text_from_chat_completion(response)
            tool_calls = _extract_chat_tool_calls(response)
        else:
            tokens_in, tokens_out, cache_read = _extract_responses_usage(response)
            text = _extract_text_from_response(response)
            tool_calls = _extract_responses_tool_calls(response)

        cost = _estimate_cost(model, tokens_in, tokens_out, cache_read)
        self._total_cost_usd += cost

        # Capture provider correlation id (response.id)
        provider_request_id = getattr(response, "id", None)

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
                latency_ms=latency_ms,
                source=f"adapter:openai:{api_kind}"
                + (f":req_id={provider_request_id}" if provider_request_id else ""),
            )
        )
        # Auto-emit tool decisions + before_tool_use
        for call_id, name, args in tool_calls:
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
                    chosen_action=f"{name}({args_repr})",
                    source="adapter:openai",
                )
            )
            self._emit(
                BeforeToolUsePayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_seq(),
                    tool_name=name,
                    args=args,
                    source="adapter:openai",
                )
            )
            self._pending_tool_calls[call_id] = (name, args, time.monotonic())

    def _emit_after_tool_use(self, call_id: str, tool_name: str, result: str) -> None:
        started = self._pending_tool_calls.pop(call_id, (tool_name, {}, time.monotonic()))[2]
        duration_ms = int((time.monotonic() - started) * 1000)
        self._emit(
            AfterToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                tool_name=tool_name,
                result=result[:4000],
                duration_ms=duration_ms,
                source="adapter:openai",
            )
        )

    def emit_error(self, error_type: str, message: str) -> None:
        self._emit(
            OnErrorPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                error_type=error_type,
                message=message[:2000],
                source="adapter:openai",
            )
        )

    # ---------- manual hooks (rare; auto-detection covers most cases) ----------

    def final_output(self, text: str, total_steps: int = 0) -> None:
        self._emit(
            OnFinalOutputPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                final_response=text[:4000],
                total_steps=total_steps,
                source="adapter:openai",
            )
        )

    def before_tool_use(self, tool_name: str, args: dict[str, Any]) -> None:
        self._emit(
            BeforeToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                tool_name=tool_name,
                args=args or {},
                source="adapter:openai",
            )
        )


# ---------- streaming accumulators -----------------------------------------


class _ChatStreamAccumulator:
    """Accumulates chat.completions stream chunks into a synthetic
    `ChatCompletion`-shaped object so `emit_after_llm` can run normally."""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        # tool_call_index -> {id, name, arguments_buffer}
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.usage: Any = None
        self.model: str | None = None
        self.response_id: str | None = None
        self.finish_reason: str | None = None

    def feed(self, chunk: Any) -> None:
        # response.id surfaces on first chunk
        if self.response_id is None:
            self.response_id = getattr(chunk, "id", None)
        if self.model is None:
            self.model = getattr(chunk, "model", None)
        choices = getattr(chunk, "choices", None) or []
        if choices:
            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta is not None:
                content = getattr(delta, "content", None)
                if isinstance(content, str) and content:
                    self.text_parts.append(content)
                tcs = getattr(delta, "tool_calls", None) or []
                for tc in tcs:
                    idx = getattr(tc, "index", 0)
                    bucket = self.tool_calls.setdefault(
                        idx, {"id": None, "name": None, "arguments": ""}
                    )
                    tc_id = getattr(tc, "id", None)
                    if tc_id:
                        bucket["id"] = tc_id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        name = getattr(fn, "name", None)
                        if name:
                            bucket["name"] = name
                        args_part = getattr(fn, "arguments", None)
                        if args_part:
                            bucket["arguments"] += args_part
            fr = getattr(choice, "finish_reason", None)
            if fr:
                self.finish_reason = fr
        # usage arrives only on the final chunk (with stream_options.include_usage)
        u = getattr(chunk, "usage", None)
        if u is not None:
            self.usage = u

    def to_response(self) -> Any:
        """Build a duck-typed `ChatCompletion` from the accumulated state.

        Compatible with `_extract_chat_usage` / `_extract_chat_tool_calls`."""
        from types import SimpleNamespace

        tool_calls_objs: list[Any] = []
        for idx in sorted(self.tool_calls):
            t = self.tool_calls[idx]
            if t.get("name"):
                tool_calls_objs.append(
                    SimpleNamespace(
                        id=t.get("id") or f"call_{idx}",
                        type="function",
                        function=SimpleNamespace(
                            name=t["name"], arguments=t.get("arguments") or ""
                        ),
                    )
                )
        msg = SimpleNamespace(
            content="".join(self.text_parts) or None,
            tool_calls=tool_calls_objs or None,
        )
        choice = SimpleNamespace(message=msg, finish_reason=self.finish_reason or "stop")
        return SimpleNamespace(
            id=self.response_id,
            model=self.model or "openai",
            choices=[choice],
            usage=self.usage,
        )


class _ResponsesStreamAccumulator:
    """Accumulates Responses-API stream events; the final `response.completed`
    event carries the full `Response` so we just stash it and return on
    extraction time."""

    def __init__(self) -> None:
        self.final_response: Any = None

    def feed(self, event: Any) -> None:
        etype = getattr(event, "type", None) or (
            event.get("type") if isinstance(event, dict) else None
        )
        if etype == "response.completed":
            self.final_response = (
                getattr(event, "response", None)
                or (event.get("response") if isinstance(event, dict) else None)
            )

    def to_response(self) -> Any:
        return self.final_response


def _make_stream_accumulator(api_kind: str):
    if api_kind == "responses":
        return _ResponsesStreamAccumulator()
    return _ChatStreamAccumulator()


class _SyncStreamWrapper:
    """Wraps a sync `Stream` so `for chunk in stream:` still works while
    we accumulate chunks for the final `after_llm_call` emission."""

    def __init__(
        self,
        stream: Any,
        emitter: _OpenAIEmitter,
        kwargs: dict[str, Any],
        path: list[str],
        t0: float,
    ) -> None:
        self._stream = stream
        self._emitter = emitter
        self._kwargs = kwargs
        self._path = path
        self._t0 = t0
        self._accumulator = _make_stream_accumulator(_api_kind_for_path(path))
        self._closed = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = next(self._stream)
        except StopIteration:
            self._close(success=True)
            raise
        except Exception as e:
            self._close(success=False, exc=e)
            raise
        self._accumulator.feed(chunk)
        return chunk

    def _close(self, *, success: bool, exc: BaseException | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        latency_ms = int((time.monotonic() - self._t0) * 1000)
        if exc is not None:
            self._emitter.emit_error("LLMStreamError", str(exc))
            return
        try:
            response = self._accumulator.to_response()
            if response is not None:
                self._emitter.emit_after_llm(
                    self._kwargs, response, latency_ms, self._path
                )
        except Exception as e:  # pragma: no cover
            log.debug("openai stream emit failed: %s", e)

    # Many SDK Stream objects support __enter__ / close
    def __enter__(self):
        if hasattr(self._stream, "__enter__"):
            self._stream.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._close(success=exc is None, exc=exc)
        finally:
            if hasattr(self._stream, "__exit__"):
                return self._stream.__exit__(exc_type, exc, tb)
            return None

    def close(self) -> None:
        try:
            self._close(success=True)
        finally:
            if hasattr(self._stream, "close"):
                self._stream.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _AsyncStreamWrapper:
    """Async sibling — wraps `AsyncStream`."""

    def __init__(
        self,
        stream: Any,
        emitter: _OpenAIEmitter,
        kwargs: dict[str, Any],
        path: list[str],
        t0: float,
    ) -> None:
        self._stream = stream
        self._emitter = emitter
        self._kwargs = kwargs
        self._path = path
        self._t0 = t0
        self._accumulator = _make_stream_accumulator(_api_kind_for_path(path))
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self._stream.__anext__()
        except StopAsyncIteration:
            self._close(success=True)
            raise
        except Exception as e:
            self._close(success=False, exc=e)
            raise
        self._accumulator.feed(chunk)
        return chunk

    def _close(self, *, success: bool, exc: BaseException | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        latency_ms = int((time.monotonic() - self._t0) * 1000)
        if exc is not None:
            self._emitter.emit_error("LLMStreamError", str(exc))
            return
        try:
            response = self._accumulator.to_response()
            if response is not None:
                self._emitter.emit_after_llm(
                    self._kwargs, response, latency_ms, self._path
                )
        except Exception as e:  # pragma: no cover
            log.debug("openai async stream emit failed: %s", e)

    async def __aenter__(self):
        if hasattr(self._stream, "__aenter__"):
            await self._stream.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            self._close(success=exc is None, exc=exc)
        finally:
            if hasattr(self._stream, "__aexit__"):
                return await self._stream.__aexit__(exc_type, exc, tb)
            return None

    async def aclose(self) -> None:
        try:
            self._close(success=True)
        finally:
            if hasattr(self._stream, "aclose"):
                await self._stream.aclose()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


# ---------- with_streaming_response wrapping -------------------------------


def _dict_to_namespace(d: Any) -> Any:
    """Recursively convert a dict (e.g. parsed SSE chunk) into a SimpleNamespace
    so the chat-stream accumulator can attribute-access fields the same way
    it does for real `ChatCompletionChunk` objects."""
    from types import SimpleNamespace

    if isinstance(d, dict):
        return SimpleNamespace(**{k: _dict_to_namespace(v) for k, v in d.items()})
    if isinstance(d, list):
        return [_dict_to_namespace(item) for item in d]
    return d


class _WithStreamingResponseProxy:
    """Wraps an APIResponse from `with_streaming_response.create()`.

    Forwards every attribute access (`.headers`, `.status_code`, `.parse()`,
    ...) to the real response, but intercepts the SSE iteration methods so
    we can accumulate chunks for the `after_llm_call` emission on `__exit__`.

    OpenAI's SSE format yields lines like `data: {...}` (one JSON-encoded
    chunk per `data:` line, plus a `data: [DONE]` terminator).  We decode
    each line and feed the parsed dict to the chat-stream accumulator —
    same code path as `_SyncStreamWrapper` for direct `stream=True` calls."""

    def __init__(self, response: Any, accumulator: Any) -> None:
        self._response = response
        self._accumulator = accumulator

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def parse(self, *args: Any, **kwargs: Any) -> Any:
        """Pass-through .parse() — also feeds the accumulator with the
        parsed final response when the user calls `.parse()` directly
        (the SDK does this for non-stream uses of with_streaming_response)."""
        parsed = self._response.parse(*args, **kwargs)
        try:
            self._accumulator.feed(parsed) if hasattr(self._accumulator, "feed") else None
        except Exception:
            pass
        return parsed

    def iter_lines(self, *args: Any, **kwargs: Any):
        for line in self._response.iter_lines(*args, **kwargs):
            self._feed_line(line)
            yield line

    def iter_text(self, *args: Any, **kwargs: Any):
        # `iter_text` concatenates lines into arbitrary text chunks.  We
        # buffer across yielded chunks because a single SSE event may be
        # split mid-line.
        buffer = ""
        for chunk in self._response.iter_text(*args, **kwargs):
            yield chunk  # pass through to user immediately
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                self._feed_line(line)

    def iter_events(self, *args: Any, **kwargs: Any):
        # `iter_events` is the SDK's typed event API — already parsed
        # ChatCompletionChunk-like objects.
        for event in self._response.iter_events(*args, **kwargs):
            try:
                self._accumulator.feed(event)
            except Exception:
                pass
            yield event

    def iter_bytes(self, *args: Any, **kwargs: Any):
        # `iter_bytes` is fully raw; we don't try to parse partial bytes.
        # Caller responsibility for decoding.  Pass through as-is.
        return self._response.iter_bytes(*args, **kwargs)

    def _feed_line(self, line: Any) -> None:
        """Decode an SSE line of the form `data: {...}` and feed the
        parsed JSON to the accumulator."""
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8", errors="replace")
            except Exception:
                return
        if not isinstance(line, str):
            return
        s = line.strip()
        if not s.startswith("data:"):
            return
        data = s[len("data:") :].strip()
        if not data or data == "[DONE]":
            return
        try:
            parsed = json.loads(data)
        except Exception:
            return
        try:
            self._accumulator.feed(_dict_to_namespace(parsed))
        except Exception:
            pass


class _AsyncWithStreamingResponseProxy(_WithStreamingResponseProxy):
    """Async sibling — exposes `aiter_lines`, `aiter_text`, `aiter_events`."""

    async def aiter_lines(self, *args: Any, **kwargs: Any):
        async for line in self._response.iter_lines(*args, **kwargs):
            self._feed_line(line)
            yield line

    async def aiter_text(self, *args: Any, **kwargs: Any):
        buffer = ""
        async for chunk in self._response.iter_text(*args, **kwargs):
            yield chunk
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                self._feed_line(line)

    async def aiter_events(self, *args: Any, **kwargs: Any):
        async for event in self._response.iter_events(*args, **kwargs):
            try:
                self._accumulator.feed(event)
            except Exception:
                pass
            yield event

    async def aparse(self, *args: Any, **kwargs: Any) -> Any:
        parsed = await self._response.parse(*args, **kwargs)
        try:
            self._accumulator.feed(parsed) if hasattr(self._accumulator, "feed") else None
        except Exception:
            pass
        return parsed


class _WithStreamingResponseWrapper:
    """Wraps the context manager returned by
    `client.chat.completions.with_streaming_response.create(...)` (sync).

    On `__enter__`, returns a proxy that forwards to the real APIResponse
    while accumulating SSE chunks.  On `__exit__`, emits the assembled
    `after_llm_call` event."""

    def __init__(
        self,
        manager: Any,
        emitter: "_OpenAIEmitter",
        kwargs: dict[str, Any],
        path: list[str],
        t0: float,
    ) -> None:
        self._manager = manager
        self._emitter = emitter
        self._kwargs = kwargs
        self._path = path
        self._t0 = t0
        self._accumulator = _make_stream_accumulator(_api_kind_for_path(path))
        self._closed = False
        self._proxy: _WithStreamingResponseProxy | None = None

    def __enter__(self) -> Any:
        response = self._manager.__enter__()
        self._proxy = _WithStreamingResponseProxy(response, self._accumulator)
        return self._proxy

    def __exit__(self, exc_type, exc, tb) -> Any:
        try:
            self._close(exc=exc)
        finally:
            return self._manager.__exit__(exc_type, exc, tb)

    def _close(self, *, exc: BaseException | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        latency_ms = int((time.monotonic() - self._t0) * 1000)
        if exc is not None:
            self._emitter.emit_error("LLMStreamError", str(exc))
            return
        try:
            response = self._accumulator.to_response()
            if response is not None:
                self._emitter.emit_after_llm(
                    self._kwargs, response, latency_ms, self._path
                )
        except Exception as e:  # pragma: no cover
            log.debug("with_streaming_response emit failed: %s", e)


class _AsyncWithStreamingResponseWrapper(_WithStreamingResponseWrapper):
    """Async sibling — supports `async with`."""

    async def __aenter__(self) -> Any:
        response = await self._manager.__aenter__()
        self._proxy = _AsyncWithStreamingResponseProxy(response, self._accumulator)
        return self._proxy

    async def __aexit__(self, exc_type, exc, tb) -> Any:
        try:
            self._close(exc=exc)
        finally:
            return await self._manager.__aexit__(exc_type, exc, tb)


# ---------- with_raw_response wrapping --------------------------------------


def _unwrap_raw_response(raw: Any) -> Any:
    """`LegacyAPIResponse` (from `with_raw_response.create`) wraps the parsed
    object — call `.parse()` to unwrap.  We attach the raw HTTP request id
    onto the parsed object so the emitter can pick it up later."""
    try:
        parsed = raw.parse()
    except Exception:
        return None
    # Pass through OpenAI's request id from the headers
    try:
        request_id = raw.headers.get("x-request-id") or raw.headers.get("openai-request-id")
        if request_id and parsed is not None:
            try:
                # Some SDK Response objects allow attribute setting; many
                # don't (Pydantic frozen).  Fall back silently.
                if not getattr(parsed, "id", None):
                    parsed.id = request_id  # type: ignore[attr-defined]
            except Exception:
                pass
    except Exception:
        pass
    return parsed


# ---------- the wrapper -----------------------------------------------------


class _MethodProxy:
    """Recursive attribute proxy.  Returns the inner attribute unmodified
    unless its full attribute path matches an LLM endpoint, in which case
    it returns a wrapped callable that emits SAFER events around the call."""

    def __init__(self, *, inner: Any, adapter: "_OpenAIAdapter", path: list[str]) -> None:
        self._inner = inner
        self._adapter = adapter
        self._path = path

    def __getattr__(self, item: str) -> Any:
        try:
            next_inner = getattr(self._inner, item)
        except AttributeError:
            raise
        next_path = [*self._path, item]
        # Wrap LLM endpoint calls
        if callable(next_inner) and _is_llm_path(next_path):
            return self._adapter._wrap_create(next_inner, next_path)
        # Drill deeper for namespaces (chat, responses, with_raw_response, ...)
        # Check by NAME rather than introspection so we walk through SDK
        # cached_property results regardless of their __dict__ shape.
        # If a known sub-namespace, return another proxy.
        # Otherwise return the bare attribute (passthrough).
        known_subnamespaces = {
            "chat", "completions", "responses",
            "with_raw_response", "with_streaming_response",
        }
        if item in known_subnamespaces or any(
            tuple(next_path + [n]) in _LLM_PATHS or tuple(next_path) in {p[: len(next_path)] for p in _LLM_PATHS}
            for n in known_subnamespaces
        ):
            return _MethodProxy(inner=next_inner, adapter=self._adapter, path=next_path)
        return next_inner


def _is_async_openai_client(client: Any) -> bool:
    """True iff `client` is `AsyncOpenAI` (or compatible).

    Modern `openai-python` decorates `AsyncCompletions.create` with overload
    machinery that hides the coroutine flag from `inspect.iscoroutinefunction`,
    so we can't probe the method directly.  The reliable signals are
    (a) `isinstance(client, AsyncOpenAI)` and (b) the presence of
    `_AsyncAPIClient` in the MRO."""
    try:
        from openai import AsyncOpenAI

        if isinstance(client, AsyncOpenAI):
            return True
    except ImportError:
        pass
    # Duck typing fallback for tests that pass plain objects with async
    # `chat.completions.create`
    import inspect as _inspect

    msgs = getattr(getattr(client, "chat", None), "completions", None)
    create = getattr(msgs, "create", None) if msgs is not None else None
    if create is not None and _inspect.iscoroutinefunction(create):
        return True
    # MRO check for SDK-internal subclasses
    return any(
        getattr(cls, "__name__", "") == "_AsyncAPIClient" for cls in type(client).__mro__
    )


class _OpenAIAdapter:
    def __init__(
        self,
        *,
        inner: Any,
        agent_id: str,
        agent_name: str,
        session_id: str | None,
    ) -> None:
        self._inner = inner
        self._is_async = _is_async_openai_client(inner)
        self._emitter = _OpenAIEmitter(
            agent_id=agent_id,
            agent_name=agent_name,
            session_id=session_id,
            safer_client=None,
        )

    @property
    def session_id(self) -> str:
        return self._emitter.session_id

    def _wrap_create(self, fn: Any, path: list[str]) -> Any:
        emitter = self._emitter
        is_with_raw = "with_raw_response" in path
        is_streaming_path = "with_streaming_response" in path

        # The OpenAI SDK's `AsyncCompletions.create` method is wrapped by
        # Stainless's overload machinery so `inspect.iscoroutinefunction`
        # returns False even though it IS an async coroutine when called.
        # Trust the per-adapter `_is_async` flag captured at construction.
        if self._is_async or inspect.iscoroutinefunction(fn):
            async def awrapped(*args: Any, **kwargs: Any) -> Any:
                emitter.emit_before_llm(kwargs, path)
                t0 = time.monotonic()
                try:
                    response = await fn(*args, **kwargs)
                except Exception as e:
                    emitter.emit_error("LLMCallError", str(e))
                    raise
                # `with_streaming_response.create` (async) — returns an
                # async context manager.  Wrap it so we accumulate SSE
                # chunks across the user's `async with` block.
                if is_streaming_path:
                    return _AsyncWithStreamingResponseWrapper(
                        response, emitter, kwargs, path, t0
                    )
                latency_ms = int((time.monotonic() - t0) * 1000)
                # Direct streaming (`stream=True` on chat.completions.create)
                if kwargs.get("stream"):
                    return _AsyncStreamWrapper(response, emitter, kwargs, path, t0)
                # with_raw_response: unwrap LegacyAPIResponse via .parse()
                if is_with_raw:
                    parsed = _unwrap_raw_response(response)
                    if parsed is not None:
                        emitter.emit_after_llm(kwargs, parsed, latency_ms, path)
                    return response
                emitter.emit_after_llm(kwargs, response, latency_ms, path)
                return response

            return awrapped

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            emitter.emit_before_llm(kwargs, path)
            t0 = time.monotonic()
            try:
                response = fn(*args, **kwargs)
            except Exception as e:
                emitter.emit_error("LLMCallError", str(e))
                raise
            # `with_streaming_response.create` returns a context manager that
            # yields an `APIResponse`.  We wrap it so we can accumulate SSE
            # chunks the user iterates via response.iter_lines/iter_events
            # and emit `after_llm_call` on context exit with the assembled
            # final ChatCompletion / Response.
            if is_streaming_path:
                return _WithStreamingResponseWrapper(
                    response, emitter, kwargs, path, t0
                )
            if kwargs.get("stream"):
                return _SyncStreamWrapper(response, emitter, kwargs, path, t0)
            latency_ms = int((time.monotonic() - t0) * 1000)
            if is_with_raw:
                parsed = _unwrap_raw_response(response)
                if parsed is not None:
                    emitter.emit_after_llm(kwargs, parsed, latency_ms, path)
                return response
            emitter.emit_after_llm(kwargs, response, latency_ms, path)
            return response

        return wrapped

    # ---------- public manual helpers ----------

    def end_session(self, success: bool = True) -> None:
        self._emitter.end_session(success=success)

    def final_output(self, text: str, total_steps: int = 0) -> None:
        self._emitter.final_output(text, total_steps)

    def before_tool_use(self, tool_name: str, args: dict[str, Any]) -> None:
        self._emitter.before_tool_use(tool_name, args)

    # ---------- attribute forwarding ----------

    def __enter__(self) -> "_OpenAIAdapter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.end_session(success=exc is None)

    def __getattr__(self, item: str) -> Any:
        inner = getattr(self._inner, item)
        # Top-level namespace?
        if item in {"chat", "responses", "beta"}:
            return _MethodProxy(inner=inner, adapter=self, path=[item])
        return inner


def wrap_openai(
    client: Any,
    *,
    agent_id: str,
    agent_name: str | None = None,
    session_id: str | None = None,
) -> _OpenAIAdapter:
    """Wrap an `OpenAI` or `AsyncOpenAI` client with SAFER instrumentation.

    Only LLM endpoint calls (`chat.completions.create`, `responses.create`,
    plus their `parse` / `with_raw_response` / `with_streaming_response`
    siblings) are instrumented.  Embeddings, files, images, batches, and
    every other namespace pass through unchanged.

    Streaming responses (`stream=True`) are accumulated chunk-by-chunk so
    `after_llm_call` carries real usage + tool-call data."""
    return _OpenAIAdapter(
        inner=client,
        agent_id=agent_id,
        agent_name=agent_name or agent_id,
        session_id=session_id,
    )


__all__ = ["wrap_openai"]
