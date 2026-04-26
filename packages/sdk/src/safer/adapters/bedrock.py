"""AWS Bedrock adapter — `wrap_bedrock(client, ...)`.

Wraps a `boto3.client("bedrock-runtime")` and emits the SAFER 9-hook
lifecycle for every `converse(...)` and `converse_stream(...)` call.
The Bedrock Converse API is the modern, model-agnostic surface (works
across Claude, Mistral, Cohere, Llama, etc.) and shares the
Anthropic-style structure with `toolUse` / `toolResult` content blocks
— so the wiring closely mirrors `claude_sdk.py`.

Usage:

    import boto3
    from safer.adapters.bedrock import wrap_bedrock

    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    client = wrap_bedrock(client, agent_id="my_agent", agent_name="My Agent")

    response = client.converse(
        modelId="anthropic.claude-haiku-4-5-20251001-v1:0",
        messages=[{"role": "user", "content": [{"text": "Hello"}]}],
    )

The wrapper is sync — boto3's standard `bedrock-runtime` is sync. For
the async `aioboto3` wrapper a future iteration can mirror this
module against `aioboto3.Session`.

Like the Anthropic raw and OpenAI raw adapters, Bedrock is *already*
chat-friendly: one wrapper instance binds to one SAFER session for the
process lifetime, with `atexit` firing `on_session_end` once. Pass
`pin_session=False` only if you genuinely want per-call session
rotation — that's the unusual case.
"""

from __future__ import annotations

import atexit
import json
import logging
import threading
import time
import uuid
from typing import Any

from ..client import SaferClient, get_client
from ..exceptions import SaferBlocked
from ..gateway_check import check_or_raise
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

log = logging.getLogger("safer.adapters.bedrock")


# ---------- pricing ----------


# Per-million-token USD pricing for the most common Bedrock-hosted models.
# Falls back to Claude Opus 4.7 pricing for unknown ids — better to
# overestimate than to silently under-account.
_BEDROCK_PRICING: dict[str, tuple[float, float, float, float]] = {
    # Anthropic on Bedrock
    "anthropic.claude-opus-4-7": (15.0, 75.0, 1.5, 18.75),
    "anthropic.claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "anthropic.claude-haiku-4-5": (0.80, 4.0, 0.08, 1.0),
    # Mistral, Cohere, Llama placeholders (rates vary by region; these
    # are sane defaults — override via the SAFER cost dashboard later).
    "mistral.": (0.50, 1.50, 0.0, 0.0),
    "cohere.": (0.50, 1.50, 0.0, 0.0),
    "meta.llama": (0.30, 0.60, 0.0, 0.0),
}


def _estimate_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    cache_read: int,
    cache_write: int,
) -> float:
    p_in, p_out, p_cr, p_cw = _BEDROCK_PRICING.get(
        model,
        next(
            (v for k, v in _BEDROCK_PRICING.items() if model.startswith(k)),
            (15.0, 75.0, 1.5, 18.75),
        ),
    )
    billable_in = max(0, tokens_in - cache_read - cache_write)
    return (
        billable_in * p_in
        + tokens_out * p_out
        + cache_read * p_cr
        + cache_write * p_cw
    ) / 1_000_000


# ---------- emitter ----------


class _BedrockEmitter:
    """Holds session state + emits SAFER events for one wrapped Bedrock client."""

    def __init__(
        self,
        *,
        agent_id: str,
        agent_name: str | None,
        session_id: str | None,
        safer_client: SaferClient | None,
        pin_session: bool = True,
    ) -> None:
        from ._bootstrap import ensure_runtime

        ensure_runtime(agent_id, agent_name, framework="bedrock")
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
        self._pin_session = pin_session
        # toolUseId -> (tool_name, args, started_ts)
        self._pending_tool_calls: dict[str, tuple[str, dict[str, Any], float]] = {}
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
            log.debug("bedrock adapter emit failed: %s", e)

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
                source="adapter:bedrock",
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
                source="adapter:bedrock",
            )
        )
        if not self._pin_session:
            self._current_session_id = None  # rotate for next session

    def _atexit_close(self) -> None:
        if self._atexit_done:
            return
        self._atexit_done = True
        try:
            self.end_session(success=True)
        except Exception:  # pragma: no cover
            pass

    # ---------- before / after LLM ----------

    def emit_before_converse(self, kwargs: dict[str, Any]) -> None:
        self._ensure_session()
        # Drain any tool_result blocks the caller embedded in the new
        # messages array — they pair against pending tool_use ids and
        # produce after_tool_use events.
        self._drain_pending_tool_results(kwargs.get("messages") or [])

        model = kwargs.get("modelId") or "unknown"
        prompt = _summarize_messages(kwargs.get("messages") or [])
        tool_config = kwargs.get("toolConfig") or {}
        tools_raw = tool_config.get("tools") if isinstance(tool_config, dict) else None
        norm_tools: list[dict[str, Any]] = []
        for t in tools_raw or []:
            spec = (t or {}).get("toolSpec") if isinstance(t, dict) else None
            if isinstance(spec, dict):
                norm_tools.append(
                    {
                        "name": str(spec.get("name") or ""),
                        "description": spec.get("description"),
                    }
                )
        infer = kwargs.get("inferenceConfig") or {}
        max_tokens = infer.get("maxTokens") if isinstance(infer, dict) else None
        temperature = infer.get("temperature") if isinstance(infer, dict) else None

        self._emit(
            BeforeLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                model=str(model),
                prompt=prompt[:8000],
                tools=norm_tools,
                temperature=temperature,
                max_tokens=max_tokens,
                source="adapter:bedrock",
            )
        )

    def emit_after_converse(
        self, kwargs: dict[str, Any], response: Any, latency_ms: int
    ) -> None:
        model = kwargs.get("modelId") or "unknown"
        usage = (response or {}).get("usage") or {}
        tokens_in = int(usage.get("inputTokens") or 0)
        tokens_out = int(usage.get("outputTokens") or 0)
        cache_read = int(usage.get("cacheReadInputTokens") or 0)
        cache_write = int(usage.get("cacheWriteInputTokens") or 0)
        cost = _estimate_cost(str(model), tokens_in, tokens_out, cache_read, cache_write)
        self._total_cost_usd += cost
        text = _extract_response_text(response)
        self._emit(
            AfterLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                model=str(model),
                response=text[:8000],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost_usd=cost,
                latency_ms=latency_ms,
                source="adapter:bedrock",
            )
        )
        # Walk the response for toolUse blocks
        self._maybe_emit_tool_use(response)
        # If this looks like a final answer (stopReason in {end_turn,
        # stop_sequence, max_tokens}), emit on_final_output too.
        stop_reason = (response or {}).get("stopReason")
        if stop_reason in {"end_turn", "stop_sequence", "max_tokens"}:
            self._emit(
                OnFinalOutputPayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_seq(),
                    final_response=text[:4000],
                    total_steps=self._step_count,
                    source="adapter:bedrock",
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
                source="adapter:bedrock",
            )
        )

    # ---------- tool_use auto-detection ----------

    def _maybe_emit_tool_use(self, response: Any) -> None:
        msg = ((response or {}).get("output") or {}).get("message") or {}
        content = msg.get("content") or []
        for block in content:
            if not isinstance(block, dict):
                continue
            tu = block.get("toolUse")
            if not isinstance(tu, dict):
                continue
            tool_id = str(tu.get("toolUseId") or f"tu_{uuid.uuid4().hex[:8]}")
            tool_name = str(tu.get("name") or "tool")
            tool_input = tu.get("input") or {}
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
                    source="adapter:bedrock",
                )
            )
            self._emit(
                BeforeToolUsePayload(
                    session_id=self.session_id,
                    agent_id=self.agent_id,
                    sequence=self._next_seq(),
                    tool_name=tool_name,
                    args=tool_input,
                    source="adapter:bedrock",
                )
            )
            self._pending_tool_calls[tool_id] = (
                tool_name,
                tool_input,
                time.monotonic(),
            )
            # Synchronous gateway check on the auto-detected toolUse.
            # User code runs the bedrock toolUse → toolResult loop, so
            # SaferBlocked propagates to the loop where the user adds a
            # tool_result error block.
            try:
                check_or_raise(
                    "before_tool_use",
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    tool_name=tool_name,
                    args=tool_input,
                )
            except SaferBlocked:
                raise
            except Exception as e:  # pragma: no cover — soft-fail
                log.debug("gateway check failed (soft-allow): %s", e)

    def _drain_pending_tool_results(self, messages: list[Any]) -> None:
        """Walk messages for `toolResult` blocks that match pending
        `toolUseId`s and pair them into after_tool_use events."""
        if not self._pending_tool_calls:
            return
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                tr = block.get("toolResult")
                if not isinstance(tr, dict):
                    continue
                tu_id = str(tr.get("toolUseId") or "")
                if not tu_id or tu_id not in self._pending_tool_calls:
                    continue
                tool_name, args, started = self._pending_tool_calls.pop(tu_id)
                duration_ms = int((time.monotonic() - started) * 1000)
                content = tr.get("content") or []
                # Concatenate all text blocks in the tool result.
                result_parts: list[str] = []
                for c in content:
                    if isinstance(c, dict):
                        if "text" in c:
                            result_parts.append(str(c["text"]))
                        elif "json" in c:
                            try:
                                result_parts.append(json.dumps(c["json"]))
                            except Exception:
                                pass
                self._emit(
                    AfterToolUsePayload(
                        session_id=self.session_id,
                        agent_id=self.agent_id,
                        sequence=self._next_seq(),
                        tool_name=tool_name,
                        result="\n".join(result_parts)[:4000],
                        duration_ms=duration_ms,
                        source="adapter:bedrock",
                    )
                )


# ---------- response helpers ----------


def _extract_response_text(response: Any) -> str:
    msg = ((response or {}).get("output") or {}).get("message") or {}
    parts: list[str] = []
    for block in msg.get("content") or []:
        if isinstance(block, dict) and "text" in block:
            parts.append(str(block["text"]))
    return "\n".join(parts).strip()


def _summarize_messages(messages: list[Any]) -> str:
    """Tiny `messages.create`-style summarization for the prompt field."""
    parts: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or "?"
        content = msg.get("content")
        if isinstance(content, list):
            text_bits = [
                c.get("text", "") if isinstance(c, dict) else "" for c in content
            ]
            parts.append(f"[{role}] " + " ".join(t for t in text_bits if t))
        elif isinstance(content, str):
            parts.append(f"[{role}] {content}")
    return "\n".join(parts)


# ---------- public wrapper ----------


class _BedrockProxy:
    """Lightweight proxy around a `bedrock-runtime` client that
    intercepts `converse` + `converse_stream`. Anything else
    (`__getattr__`) forwards transparently."""

    def __init__(self, client: Any, emitter: _BedrockEmitter) -> None:
        self._client = client
        self._emitter = emitter

    def __getattr__(self, name: str) -> Any:
        # Only wire converse + converse_stream; everything else passes
        # through unchanged so the wrapped client behaves identically.
        if name == "converse":
            return self._converse
        if name == "converse_stream":
            return self._converse_stream
        return getattr(self._client, name)

    @property
    def session_id(self) -> str:
        return self._emitter.session_id

    @property
    def emitter(self) -> _BedrockEmitter:
        return self._emitter

    def end_session(self, *, success: bool = True) -> None:
        self._emitter.end_session(success=success)

    def close_session(self, *, success: bool = True) -> None:
        """Alias for `end_session` matching the other adapters."""
        self._emitter.end_session(success=success)

    def _converse(self, **kwargs: Any) -> Any:
        self._emitter.emit_before_converse(kwargs)
        t0 = time.monotonic()
        try:
            response = self._client.converse(**kwargs)
        except Exception as e:
            self._emitter.emit_error(type(e).__name__, str(e))
            raise
        latency_ms = int((time.monotonic() - t0) * 1000)
        self._emitter.emit_after_converse(kwargs, response, latency_ms)
        return response

    def _converse_stream(self, **kwargs: Any) -> Any:
        self._emitter.emit_before_converse(kwargs)
        t0 = time.monotonic()
        try:
            response = self._client.converse_stream(**kwargs)
        except Exception as e:
            self._emitter.emit_error(type(e).__name__, str(e))
            raise
        # The streaming response holds a generator of events; we wrap
        # it so we can accumulate text + usage + tool_use blocks before
        # emitting `after_llm_call`. Bedrock's stream event vocabulary
        # is `messageStart`, `contentBlockStart`, `contentBlockDelta`,
        # `contentBlockStop`, `messageStop`, `metadata`.
        stream = response.get("stream") if isinstance(response, dict) else None
        if stream is None:
            # Caller-side iterates differently; we already emitted
            # before_converse but cannot accumulate — drop with no
            # after event.
            return response

        emitter = self._emitter

        def _wrap_stream():
            text_parts: list[str] = []
            usage = {
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheReadInputTokens": 0,
                "cacheWriteInputTokens": 0,
            }
            stop_reason = None
            tool_use_acc: dict[int, dict[str, Any]] = {}
            for ev in stream:
                yield ev
                if not isinstance(ev, dict):
                    continue
                if "contentBlockStart" in ev:
                    blk = (ev["contentBlockStart"] or {}).get("start") or {}
                    tu = blk.get("toolUse") if isinstance(blk, dict) else None
                    if isinstance(tu, dict):
                        idx = (ev["contentBlockStart"] or {}).get(
                            "contentBlockIndex"
                        )
                        tool_use_acc[idx] = {
                            "toolUseId": tu.get("toolUseId"),
                            "name": tu.get("name"),
                            "input_json": "",
                        }
                elif "contentBlockDelta" in ev:
                    delta = (ev["contentBlockDelta"] or {}).get("delta") or {}
                    if "text" in delta:
                        text_parts.append(str(delta["text"]))
                    tu_delta = delta.get("toolUse")
                    if isinstance(tu_delta, dict):
                        idx = (ev["contentBlockDelta"] or {}).get(
                            "contentBlockIndex"
                        )
                        if idx in tool_use_acc:
                            tool_use_acc[idx]["input_json"] += str(
                                tu_delta.get("input", "") or ""
                            )
                elif "messageStop" in ev:
                    stop_reason = (ev["messageStop"] or {}).get("stopReason")
                elif "metadata" in ev:
                    u = (ev["metadata"] or {}).get("usage") or {}
                    for k in usage:
                        if k in u and u[k] is not None:
                            usage[k] = int(u[k])

            # Build a synthetic "non-stream" response shape so the
            # after_converse path can reuse the standard parser.
            content_blocks: list[dict[str, Any]] = []
            full_text = "".join(text_parts)
            if full_text:
                content_blocks.append({"text": full_text})
            for idx, acc in tool_use_acc.items():
                try:
                    parsed_input = (
                        json.loads(acc["input_json"]) if acc["input_json"] else {}
                    )
                except Exception:
                    parsed_input = {"_raw": acc["input_json"]}
                content_blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": acc["toolUseId"],
                            "name": acc["name"],
                            "input": parsed_input,
                        }
                    }
                )
            synthetic = {
                "output": {
                    "message": {"role": "assistant", "content": content_blocks}
                },
                "usage": usage,
                "stopReason": stop_reason,
            }
            latency_ms = int((time.monotonic() - t0) * 1000)
            emitter.emit_after_converse(kwargs, synthetic, latency_ms)

        return {"stream": _wrap_stream(), **{k: v for k, v in response.items() if k != "stream"}}


def wrap_bedrock(
    client: Any,
    *,
    agent_id: str,
    agent_name: str | None = None,
    session_id: str | None = None,
    safer_client: SaferClient | None = None,
    pin_session: bool = True,
) -> Any:
    """Return a SAFER-instrumented proxy of a `bedrock-runtime` boto3 client.

    Every `converse(...)` / `converse_stream(...)` call from the proxy
    emits the SAFER 9-hook lifecycle. All other methods on the
    underlying client (`invoke_model`, paginators, exceptions, etc.)
    pass through unchanged.

    Pass `pin_session=False` to rotate session_id per `end_session()`
    call — by default the wrapper behaves like the Anthropic / OpenAI
    raw adapters and pins the session for the wrapper's lifetime
    (closing once via atexit).
    """
    emitter = _BedrockEmitter(
        agent_id=agent_id,
        agent_name=agent_name,
        session_id=session_id,
        safer_client=safer_client,
        pin_session=pin_session,
    )
    return _BedrockProxy(client, emitter)


__all__ = ["wrap_bedrock"]
