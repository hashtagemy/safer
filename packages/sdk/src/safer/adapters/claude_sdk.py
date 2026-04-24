"""Claude / Anthropic client-proxy adapter.

Wraps `anthropic.Anthropic` (or `AsyncAnthropic`) so every
`messages.create` emits `before_llm_call` + `after_llm_call` hooks
automatically. The raw Anthropic SDK has no native hook surface, so the
remaining SAFER lifecycle hooks (`before_tool_use`, `after_tool_use`,
`on_agent_decision`, `on_final_output`, `on_session_end`, `on_error`)
must be emitted via the tracker's helper methods
(`tracker.before_tool_use(...)`, `tracker.final_output(...)`, ...).

**For zero-config full observability on raw Anthropic code, prefer the
OTel bridge** (`safer.adapters.otel.configure_otel_bridge`) — it drops
in `opentelemetry-instrumentation-anthropic` which emits all nine
SAFER hooks (via GenAI span parsing on the backend) with no manual
helper calls.

Usage:

    from anthropic import Anthropic
    from safer import instrument
    from safer.adapters.claude_sdk import wrap_anthropic

    instrument()
    client = Anthropic()
    agent = wrap_anthropic(client, agent_id="support", agent_name="Customer Support")

    agent.start_session(context={"user": "alice"})
    response = agent.messages.create(model="claude-opus-4-7", ...)
    # → before_llm_call + after_llm_call emitted

    agent.before_tool_use("get_order", {"id": 123})
    result = get_order(123)
    agent.after_tool_use("get_order", result)

    agent.final_output("Your order has shipped.")
    agent.end_session(success=True)
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
    Hook,
    OnAgentDecisionPayload,
    OnErrorPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnSessionStartPayload,
)

log = logging.getLogger("safer.adapters.claude")

# Opus 4.7 approximate pricing (USD / 1M tokens). Keep in sync with
# Anthropic pricing updates. This is best-effort; the real cost is
# authoritative via Anthropic's billing.
_PRICING: dict[str, tuple[float, float, float, float]] = {
    # (input, output, cache_read, cache_write) per 1M tokens
    "claude-opus-4-7": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4-5": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5": (0.80, 4.0, 0.08, 1.0),
}


def _estimate_cost_usd(
    model: str, tokens_in: int, tokens_out: int, cache_read: int, cache_write: int
) -> float:
    pricing = _PRICING.get(model)
    if pricing is None:
        # Unknown model; default to Opus pricing to over-estimate.
        pricing = _PRICING["claude-opus-4-7"]
    p_in, p_out, p_cr, p_cw = pricing
    # Billable input = tokens_in - cache_read - cache_write (cache parts billed separately)
    billable_in = max(0, tokens_in - cache_read - cache_write)
    return (
        (billable_in * p_in)
        + (tokens_out * p_out)
        + (cache_read * p_cr)
        + (cache_write * p_cw)
    ) / 1_000_000


class _MessagesProxy:
    """Wraps `anthropic.Anthropic.messages` so .create emits hooks."""

    def __init__(self, messages: Any, tracker: "AnthropicTracker") -> None:
        self._messages = messages
        self._tracker = tracker

    def create(self, **kwargs: Any) -> Any:
        """Synchronous create with hook instrumentation."""
        self._tracker._emit_before_llm(kwargs)
        t0 = time.monotonic()
        try:
            response = self._messages.create(**kwargs)
        except Exception as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._tracker._emit_error("LLMCallError", str(e))
            raise
        latency_ms = int((time.monotonic() - t0) * 1000)
        self._tracker._emit_after_llm(kwargs, response, latency_ms)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._messages, name)


class AnthropicTracker:
    """Wraps an anthropic.Anthropic client and exposes SAFER lifecycle hooks.

    Most attribute access is delegated to the wrapped client, so you can
    still use `tracker.models.list()` or any non-instrumented feature.
    Only `tracker.messages.create()` is intercepted.
    """

    def __init__(
        self,
        client: Any,
        agent_id: str,
        agent_name: str | None = None,
        session_id: str | None = None,
        safer: SaferClient | None = None,
    ) -> None:
        from ._bootstrap import ensure_runtime

        ensure_runtime(agent_id, agent_name)
        self._client = client
        self.agent_id = agent_id
        self.agent_name = agent_name or agent_id
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        self._safer = safer
        self._step = 0
        self._session_started = False
        self._session_start_ts: float | None = None
        self._total_cost_usd = 0.0
        self._profile_synced = False

        # Wrap messages proxy; other attributes pass through.
        self.messages = _MessagesProxy(client.messages, self)

    # ---------- Public hook helpers ----------

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
        duration = int((time.monotonic() - (self._session_start_ts or time.monotonic())) * 1000)
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
                total_steps=total_steps or self._step,
                source="adapter:claude_sdk",
            )
        )

    # ---------- internal ----------

    def _next_seq(self) -> int:
        client = self._safer or get_client()
        if client is not None:
            return client.next_sequence(self.session_id)
        n = self._step
        self._step += 1
        return n

    def _emit(self, event: Any) -> None:
        client = self._safer or get_client()
        if client is None:
            log.debug("SAFER not initialized; dropping event %s", type(event).__name__)
            return
        client.emit(event)

    def _emit_before_llm(self, kwargs: dict[str, Any]) -> None:
        model = kwargs.get("model", "unknown")
        messages = kwargs.get("messages", [])
        prompt = _summarize_messages(messages)
        tools = kwargs.get("tools") or []
        self._maybe_sync_profile(kwargs.get("system"))
        self._emit(
            BeforeLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                model=model,
                prompt=prompt,
                tools=list(tools) if tools else [],
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens"),
                source="adapter:claude_sdk",
            )
        )

    def _maybe_sync_profile(self, system: Any) -> None:
        """First time we see a non-empty system= argument on messages.create,
        PATCH it to /v1/agents/{id}/profile so the dashboard can show the
        real system prompt without the user wiring it manually."""
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
        except Exception as e:  # pragma: no cover — defensive
            log.debug("SAFER: profile sync failed for %s: %s", self.agent_id, e)

    def _emit_after_llm(self, kwargs: dict[str, Any], response: Any, latency_ms: int) -> None:
        model = kwargs.get("model", "unknown")
        response_text = _extract_response_text(response)
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
        cost = _estimate_cost_usd(model, tokens_in, tokens_out, cache_read, cache_write)
        self._total_cost_usd += cost
        self._emit(
            AfterLLMCallPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                model=model,
                response=response_text,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost_usd=cost,
                latency_ms=latency_ms,
                source="adapter:claude_sdk",
            )
        )

    def _emit_error(self, error_type: str, message: str) -> None:
        self._emit(
            OnErrorPayload(
                session_id=self.session_id,
                agent_id=self.agent_id,
                sequence=self._next_seq(),
                error_type=error_type,
                message=message,
                source="adapter:claude_sdk",
            )
        )

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
    """Public helper — wrap an anthropic.Anthropic client with SAFER hooks."""
    return AnthropicTracker(
        client=client,
        agent_id=agent_id,
        agent_name=agent_name,
        session_id=session_id,
    )


# ---------- helpers ----------


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
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_chunks.append(block.get("text", ""))
                    else:
                        text_chunks.append(f"<{block.get('type', 'block')}>")
                else:
                    t = _get(block, "type", None)
                    if t == "text":
                        text_chunks.append(_get(block, "text", ""))
                    else:
                        text_chunks.append(f"<{t or 'block'}>")
            content_str = "\n".join(text_chunks)
        else:
            content_str = str(content)
        lines.append(f"[{role}] {content_str}")
    return "\n".join(lines)


def _extract_response_text(response: Any) -> str:
    """Pull text from an anthropic Message response, tolerant to shape."""
    content = _get(response, "content", [])
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        t = _get(block, "type", None)
        if t == "text":
            parts.append(_get(block, "text", ""))
        elif t == "tool_use":
            name = _get(block, "name", "")
            parts.append(f"[tool_use:{name}]")
    return "\n".join(parts)


def _get(obj: Any, attr: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _normalize_system_param(system: Any) -> str:
    """Turn Anthropic's system= (str or list of text blocks) into one string."""
    if system is None:
        return ""
    if isinstance(system, str):
        return system.strip()
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text = block.get("text")
                    if text:
                        parts.append(str(text))
            else:
                t = getattr(block, "type", None)
                if t == "text":
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(str(text))
        return "\n".join(parts).strip()
    return str(system).strip()
