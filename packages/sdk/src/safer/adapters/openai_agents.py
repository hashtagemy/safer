"""Partial OpenAI / OpenAI Agents SDK adapter.

Covers the two highest-value hooks — `before_llm_call` and
`after_llm_call` — by proxying the client's `chat.completions.create`
and `responses.create` methods. Tool-use and agent-decision hooks emit
a one-time "coming soon" warning; users can bridge them by invoking
`safer.track_event()` manually for now.

Usage:

    from openai import OpenAI
    from safer.adapters.openai_agents import wrap_openai

    client = wrap_openai(OpenAI(), agent_id="assistant",
                         agent_name="Support Bot")
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
    )
    # → before_llm_call + after_llm_call emitted to SAFER.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..client import get_client
from ..events import (
    AfterLLMCallPayload,
    BeforeLLMCallPayload,
    Hook,
    OnAgentDecisionPayload,
    OnErrorPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnSessionStartPayload,
)

log = logging.getLogger("safer.adapters.openai")

_WARNED = False


def _warn_once(feature: str) -> None:
    global _WARNED
    if _WARNED:
        return
    _WARNED = True
    log.warning(
        "safer.openai_agents adapter: %s is not yet bridged automatically. "
        "Use safer.track_event(Hook.%s, {...}) to emit it manually.",
        feature,
        feature,
    )


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    # Rough public pricing (per 1M tokens) — signal only.
    pricing: dict[str, tuple[float, float]] = {
        "gpt-4o": (2.5, 10.0),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4-turbo": (10.0, 30.0),
        "gpt-4": (30.0, 60.0),
        "o1-mini": (3.0, 12.0),
    }
    p_in, p_out = pricing.get(model, (5.0, 15.0))
    return ((tokens_in * p_in) + (tokens_out * p_out)) / 1_000_000


def _model_prompt(kwargs: dict[str, Any]) -> tuple[str, str]:
    model = str(kwargs.get("model") or "openai")
    messages = kwargs.get("messages") or kwargs.get("input") or []
    if isinstance(messages, str):
        prompt = messages
    else:
        parts: list[str] = []
        for m in messages:
            if isinstance(m, dict):
                content = m.get("content")
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and "text" in c:
                            parts.append(str(c["text"]))
        prompt = "\n".join(parts)
    return model, prompt[:8000]


def _extract_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if not usage:
        return 0, 0
    get = usage.get if isinstance(usage, dict) else lambda k: getattr(usage, k, 0)
    tokens_in = int(get("prompt_tokens") or get("input_tokens") or 0)
    tokens_out = int(get("completion_tokens") or get("output_tokens") or 0)
    return tokens_in, tokens_out


def _extract_text(response: Any) -> str:
    try:
        choices = getattr(response, "choices", None)
        if choices:
            first = choices[0]
            msg = getattr(first, "message", None)
            if msg is not None:
                content = getattr(msg, "content", None)
                if isinstance(content, str):
                    return content
            text = getattr(first, "text", None)
            if isinstance(text, str):
                return text
        # Responses API
        output = getattr(response, "output", None)
        if output:
            parts: list[str] = []
            for block in output:
                content = getattr(block, "content", None)
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "output_text":
                            parts.append(str(c.get("text", "")))
            if parts:
                return "\n".join(parts)
    except Exception:  # pragma: no cover
        pass
    return ""


class _MethodProxy:
    def __init__(
        self,
        *,
        inner: Any,
        adapter: "_OpenAIAdapter",
        attr_path: list[str],
    ) -> None:
        self._inner = inner
        self._adapter = adapter
        self._attr_path = attr_path

    def __getattr__(self, item: str) -> Any:
        next_inner = getattr(self._inner, item)
        path = [*self._attr_path, item]
        if callable(next_inner) and path[-1] == "create":
            return self._adapter._wrap_create(next_inner)
        if callable(next_inner) or not hasattr(next_inner, "__dict__"):
            return next_inner
        return _MethodProxy(inner=next_inner, adapter=self._adapter, attr_path=path)


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
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:16]}"
        self._sequence = 0
        self._session_started = False

    # ---------- helpers ----------

    def _next_seq(self) -> int:
        s = self._sequence
        self._sequence += 1
        return s

    def _emit(self, payload: Any) -> None:
        client = get_client()
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
            log.debug("openai adapter emit failed: %s", e)

    def _ensure_session(self) -> None:
        if self._session_started:
            return
        self._session_started = True
        self._emit(
            OnSessionStartPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                agent_name=self.agent_name,
            )
        )

    def _wrap_create(self, fn: Any) -> Any:
        adapter = self

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            adapter._ensure_session()
            model, prompt = _model_prompt(kwargs)
            adapter._emit(
                BeforeLLMCallPayload(
                    session_id=adapter.session_id,
                    agent_id=adapter.agent_id,
                    sequence=adapter._next_seq(),
                    model=model,
                    prompt=prompt,
                    tools=[],
                    temperature=kwargs.get("temperature"),
                )
            )
            t0 = time.monotonic()
            try:
                response = fn(*args, **kwargs)
            except Exception as e:
                adapter._emit(
                    OnErrorPayload(
                        session_id=adapter.session_id,
                        agent_id=adapter.agent_id,
                        sequence=adapter._next_seq(),
                        error_type=type(e).__name__,
                        message=str(e)[:2000],
                    )
                )
                raise
            latency_ms = int((time.monotonic() - t0) * 1000)
            tokens_in, tokens_out = _extract_usage(response)
            text = _extract_text(response)
            cost = _estimate_cost(model, tokens_in, tokens_out)
            adapter._emit(
                AfterLLMCallPayload(
                    session_id=adapter.session_id,
                    agent_id=adapter.agent_id,
                    sequence=adapter._next_seq(),
                    model=model,
                    response=text[:8000],
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                )
            )
            return response

        return wrapped

    # ---------- manual stubs ----------

    def before_tool_use(self, tool_name: str, args: dict[str, Any]) -> None:
        _warn_once("BEFORE_TOOL_USE")
        # Provided here as a convenience — emits the event if the user wants.
        from ..events import BeforeToolUsePayload

        self._emit(
            BeforeToolUsePayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                tool_name=tool_name,
                args=args,
            )
        )

    def final_output(self, text: str) -> None:
        self._emit(
            OnFinalOutputPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                final_response=text[:4000],
            )
        )

    def end_session(self, success: bool = True) -> None:
        self._emit(
            OnSessionEndPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                success=success,
            )
        )
        self._session_started = False

    # ---------- attribute forwarding ----------

    def __getattr__(self, item: str) -> Any:
        inner = getattr(self._inner, item)
        if callable(inner) or not hasattr(inner, "__dict__"):
            return inner
        return _MethodProxy(inner=inner, adapter=self, attr_path=[item])


def wrap_openai(
    client: Any,
    *,
    agent_id: str,
    agent_name: str | None = None,
    session_id: str | None = None,
) -> _OpenAIAdapter:
    """Wrap an OpenAI client so `chat.completions.create` and
    `responses.create` emit before/after_llm_call hooks to SAFER.

    Tool / decision / final-output hooks are stubs — call
    `.before_tool_use(...)`, `.final_output(...)`, `.end_session(...)`
    on the wrapper for now, or use `safer.track_event()` directly.
    """
    return _OpenAIAdapter(
        inner=client,
        agent_id=agent_id,
        agent_name=agent_name or agent_id,
        session_id=session_id,
    )


__all__ = ["wrap_openai"]
