"""OTLP GenAI span parser — bridges OpenTelemetry instrumentation
(e.g. `opentelemetry-instrumentation-anthropic`,
`opentelemetry-instrumentation-openai`) into SAFER's 9-hook event model.

This module owns the real work behind `POST /v1/traces`. A remote
OpenTelemetry exporter sends `ExportTraceServiceRequest` payloads (either
`application/x-protobuf` or `application/json`). We decode them, walk
the resource → scope → span hierarchy, and emit SAFER events per span
based on GenAI semantic conventions.

**GenAI attribute conventions we recognize** (stable v1.29+ + earlier
community variants):

  * `gen_ai.system`              — "anthropic" / "openai" / ...
  * `gen_ai.operation.name`      — "chat" / "text_completion" / "execute_tool"
  * `gen_ai.request.model`
  * `gen_ai.response.model`
  * `gen_ai.usage.input_tokens`  (alias: `llm.usage.prompt_tokens`)
  * `gen_ai.usage.output_tokens` (alias: `llm.usage.completion_tokens`)
  * `gen_ai.usage.cached_input_tokens`
  * `gen_ai.tool.name`           (alias: `tool.name`)
  * `gen_ai.tool.call.id`

**Span → SAFER hook mapping**:

  * Root span of a trace (no parent_span_id) on start → `on_session_start`.
  * `gen_ai.operation.name == "chat"` or span-name begins with "chat " →
    `before_llm_call` (at start) + `after_llm_call` (at end).
  * `gen_ai.operation.name == "execute_tool"` or span-name begins with
    "execute_tool " → `before_tool_use` + `after_tool_use`.
  * Span status ERROR (any span) → extra `on_error`.
  * Root span end → `on_final_output` (best-effort from
    `gen_ai.assistant.message` events) + `on_session_end`.

Session tracking is in-process only: a `TraceTracker` remembers which
trace_ids have been seen and which root spans have been closed so we
only emit start/end once per trace. The backend restarts wipe this
state — fine for an MVP because OTel exporters always resend the root
span when the trace finishes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Iterable

from google.protobuf import json_format
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan
from opentelemetry.proto.trace.v1.trace_pb2 import Status as PbStatus

from safer._pricing import estimate_cost
from safer.events import (
    AfterLLMCallPayload,
    AfterToolUsePayload,
    BeforeLLMCallPayload,
    BeforeToolUsePayload,
    Event,
    Hook,
    OnAgentDecisionPayload,
    OnErrorPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnSessionStartPayload,
)

log = logging.getLogger("safer.ingestion.otlp")


# ---------- protobuf helpers ------------------------------------------


def _any_value_to_python(v: AnyValue) -> Any:
    kind = v.WhichOneof("value")
    if kind is None:
        return None
    if kind == "string_value":
        return v.string_value
    if kind == "bool_value":
        return v.bool_value
    if kind == "int_value":
        return int(v.int_value)
    if kind == "double_value":
        return float(v.double_value)
    if kind == "array_value":
        return [_any_value_to_python(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _any_value_to_python(kv.value) for kv in v.kvlist_value.values}
    if kind == "bytes_value":
        return v.bytes_value
    return None


def _attributes_to_dict(kvs: Iterable[KeyValue]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kv in kvs:
        out[kv.key] = _any_value_to_python(kv.value)
    return out


def _hex(id_bytes: bytes) -> str:
    return id_bytes.hex() if id_bytes else ""


def _ns_to_utc(ns: int) -> float:
    """Convert unix-nanoseconds to seconds-since-epoch (float)."""
    return ns / 1_000_000_000 if ns else 0.0


# ---------- parsed span dataclass -------------------------------------


@dataclass
class ParsedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any]
    events: list[dict[str, Any]]
    status_code: int  # 0=unset, 1=ok, 2=error
    status_message: str
    resource_attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return max(0, int((self.end_ns - self.start_ns) / 1_000_000))

    @property
    def is_root(self) -> bool:
        return not self.parent_span_id

    @property
    def is_error(self) -> bool:
        return self.status_code == PbStatus.STATUS_CODE_ERROR


def parse_otlp_request(body: bytes, content_type: str) -> list[ParsedSpan]:
    """Decode an OTLP ExportTraceServiceRequest into ParsedSpan objects.

    Accepts protobuf (`application/x-protobuf`) or JSON
    (`application/json`). Raises `ValueError` on malformed input — the
    HTTP handler turns that into a 400.
    """
    req = ExportTraceServiceRequest()
    ct = (content_type or "").lower()
    try:
        if "application/json" in ct:
            json_format.Parse(body.decode("utf-8"), req)
        else:
            req.ParseFromString(body)
    except Exception as e:
        raise ValueError(f"malformed OTLP payload: {e}") from e

    out: list[ParsedSpan] = []
    for rs in req.resource_spans:
        resource_attrs = (
            _attributes_to_dict(rs.resource.attributes)
            if rs.HasField("resource")
            else {}
        )
        for scope_span in rs.scope_spans:
            for span in scope_span.spans:
                out.append(_pb_span_to_parsed(span, resource_attrs))
    return out


def _pb_span_to_parsed(
    span: PbSpan, resource_attrs: dict[str, Any]
) -> ParsedSpan:
    events: list[dict[str, Any]] = []
    for ev in span.events:
        events.append(
            {
                "name": ev.name,
                "time_ns": int(ev.time_unix_nano),
                "attributes": _attributes_to_dict(ev.attributes),
            }
        )
    return ParsedSpan(
        trace_id=_hex(span.trace_id),
        span_id=_hex(span.span_id),
        parent_span_id=_hex(span.parent_span_id),
        name=span.name,
        start_ns=int(span.start_time_unix_nano),
        end_ns=int(span.end_time_unix_nano),
        attributes=_attributes_to_dict(span.attributes),
        events=events,
        status_code=int(span.status.code) if span.HasField("status") else 0,
        status_message=span.status.message if span.HasField("status") else "",
        resource_attributes=resource_attrs,
    )


# ---------- GenAI semconv → SAFER hook mapping ------------------------

# Alias sets: older instrumentors used `llm.*`, newer use `gen_ai.*`.
_INPUT_TOKEN_KEYS = (
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.prompt_tokens",
    "llm.usage.prompt_tokens",
)
_OUTPUT_TOKEN_KEYS = (
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.completion_tokens",
    "llm.usage.completion_tokens",
)
_CACHED_TOKEN_KEYS = (
    "gen_ai.usage.cached_input_tokens",
    "gen_ai.usage.cache_read_input_tokens",
    # Newer GenAI semconv (1.36+) and OpenLLMetry forks use these too:
    "gen_ai.usage.cache_read_tokens",
    "gen_ai.prompt_cache.cached_input_tokens",
    "llm.usage.cache_read_input_tokens",
)


def _first_int(attrs: dict[str, Any], keys: Iterable[str]) -> int:
    for k in keys:
        v = attrs.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return 0


def _operation_name(span: ParsedSpan) -> str:
    op = span.attributes.get("gen_ai.operation.name")
    if isinstance(op, str) and op:
        return op.lower()
    # Fall back to span-name prefix: "chat foo-model" / "execute_tool bar".
    prefix = span.name.split(" ", 1)[0].lower() if span.name else ""
    return prefix


def _model_name(span: ParsedSpan) -> str:
    for key in ("gen_ai.response.model", "gen_ai.request.model", "llm.model_name"):
        v = span.attributes.get(key)
        if isinstance(v, str) and v:
            return v
    return "unknown"


def _tool_name(span: ParsedSpan) -> str:
    for key in ("gen_ai.tool.name", "tool.name"):
        v = span.attributes.get(key)
        if isinstance(v, str) and v:
            return v
    # fallback: span name "execute_tool foo"
    parts = span.name.split(" ", 1)
    return parts[1] if len(parts) == 2 else "tool"


def _extract_user_prompt(span: ParsedSpan) -> str:
    for ev in span.events:
        if ev["name"] in ("gen_ai.user.message", "gen_ai.prompt"):
            content = ev["attributes"].get("content")
            if isinstance(content, str):
                return content
    prompt = span.attributes.get("gen_ai.prompt") or span.attributes.get(
        "llm.prompts"
    )
    if isinstance(prompt, str):
        return prompt
    return ""


def _extract_assistant_text(span: ParsedSpan) -> str:
    for ev in reversed(span.events):
        if ev["name"] in ("gen_ai.assistant.message", "gen_ai.completion"):
            content = ev["attributes"].get("content")
            if isinstance(content, str):
                return content
    completion = span.attributes.get("gen_ai.completion") or span.attributes.get(
        "llm.completion"
    )
    if isinstance(completion, str):
        return completion
    return ""


# ---------- trace tracking -------------------------------------------


@dataclass
class _TraceState:
    session_started: bool = False
    session_ended: bool = False
    last_sequence: int = 0
    agent_id: str = "otel-agent"
    agent_name: str = "otel-agent"


class TraceTracker:
    """Keeps per-trace session bookkeeping across OTLP batches.

    OTel exporters may ship spans from the same trace across multiple
    POSTs (root span often arrives last). We de-dupe session_start so
    it fires only once per trace_id, and we emit session_end only when
    we've seen the root span close.
    """

    def __init__(self) -> None:
        self._state: dict[str, _TraceState] = {}
        self._lock = Lock()

    def next_sequence(self, trace_id: str) -> int:
        with self._lock:
            state = self._state.setdefault(trace_id, _TraceState())
            seq = state.last_sequence
            state.last_sequence += 1
            return seq

    def claim_session_start(self, trace_id: str) -> bool:
        with self._lock:
            state = self._state.setdefault(trace_id, _TraceState())
            if state.session_started:
                return False
            state.session_started = True
            return True

    def claim_session_end(self, trace_id: str) -> bool:
        with self._lock:
            state = self._state.setdefault(trace_id, _TraceState())
            if state.session_ended:
                return False
            state.session_ended = True
            return True

    def identify(
        self, trace_id: str, agent_id: str, agent_name: str
    ) -> None:
        with self._lock:
            state = self._state.setdefault(trace_id, _TraceState())
            state.agent_id = agent_id
            state.agent_name = agent_name

    def forget(self, trace_id: str) -> None:
        with self._lock:
            self._state.pop(trace_id, None)


_TRACKER = TraceTracker()


def _reset_tracker_for_tests() -> None:
    """Test hook — discard all remembered trace state."""
    _TRACKER._state.clear()


# ---------- mapping --------------------------------------------------


def _resolve_identity(span: ParsedSpan) -> tuple[str, str]:
    """Infer (agent_id, agent_name) from resource / span attributes.

    Precedence: `safer.agent_id` / `safer.agent_name` custom attrs →
    `service.name` (OTel standard) → `gen_ai.system` → "otel-agent".
    """
    attrs = {**span.resource_attributes, **span.attributes}
    agent_id = (
        attrs.get("safer.agent_id")
        or attrs.get("service.name")
        or attrs.get("gen_ai.system")
        or "otel-agent"
    )
    agent_name = (
        attrs.get("safer.agent_name")
        or attrs.get("service.instance.id")
        or agent_id
    )
    return str(agent_id), str(agent_name)


def map_genai_span_to_safer(
    span: ParsedSpan,
    tracker: TraceTracker | None = None,
) -> list[Event]:
    """Produce SAFER events for a single parsed span. Returns an empty
    list if the span is not recognized as a GenAI span (unknown
    operation / not in our mapping)."""
    t = tracker or _TRACKER
    events: list[Event] = []

    session_id = f"otel_{span.trace_id[:16]}" if span.trace_id else "otel_unknown"
    agent_id, agent_name = _resolve_identity(span)
    t.identify(span.trace_id, agent_id, agent_name)

    op = _operation_name(span)
    is_chat = op in ("chat", "text_completion", "completion", "generate_content")
    is_tool = op in ("execute_tool", "invoke_tool", "tool")

    # 1. on_session_start on first span of this trace.
    if t.claim_session_start(span.trace_id):
        events.append(
            OnSessionStartPayload(
                session_id=session_id,
                agent_id=agent_id,
                sequence=t.next_sequence(span.trace_id),
                agent_name=agent_name,
                context={
                    "source": "otel",
                    "trace_id": span.trace_id,
                    "system": span.attributes.get("gen_ai.system"),
                },
                source="otlp",
            )
        )

    # 2. LLM call pair.
    if is_chat:
        model = _model_name(span)
        tokens_in = _first_int(span.attributes, _INPUT_TOKEN_KEYS)
        tokens_out = _first_int(span.attributes, _OUTPUT_TOKEN_KEYS)
        cached = _first_int(span.attributes, _CACHED_TOKEN_KEYS)
        prompt = _extract_user_prompt(span)
        response = _extract_assistant_text(span)
        # Cost enrichment via the shared pricing table — earlier versions
        # left this hardcoded at $0, so OTel-bridge sessions had no cost
        # data on the dashboard.
        cost = estimate_cost(model, tokens_in=tokens_in, tokens_out=tokens_out, cache_read=cached) or 0.0
        events.append(
            BeforeLLMCallPayload(
                session_id=session_id,
                agent_id=agent_id,
                sequence=t.next_sequence(span.trace_id),
                model=model,
                prompt=prompt[:8000],
                tools=[],
                source="otlp",
            )
        )
        events.append(
            AfterLLMCallPayload(
                session_id=session_id,
                agent_id=agent_id,
                sequence=t.next_sequence(span.trace_id),
                model=model,
                response=response[:8000],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cache_read_tokens=cached,
                cost_usd=cost,
                latency_ms=span.duration_ms,
                source="otlp",
            )
        )
        # Best-effort tool_use synthesis — when the chat span carries a
        # `gen_ai.tool.call.id` attribute (Anthropic instrumentor emits
        # this on the parent span when the model returns a tool_use block),
        # synthesize an on_agent_decision so SAFER's Multi-Persona Judge
        # routes correctly even though the user's tool execution itself
        # is not OTel-instrumented.
        tc_id = span.attributes.get("gen_ai.tool.call.id")
        tc_name = span.attributes.get("gen_ai.tool.call.name") or span.attributes.get("gen_ai.tool.name")
        if isinstance(tc_id, str) and tc_id and isinstance(tc_name, str) and tc_name:
            events.append(
                OnAgentDecisionPayload(
                    session_id=session_id,
                    agent_id=agent_id,
                    sequence=t.next_sequence(span.trace_id),
                    decision_type="tool_call",
                    chosen_action=tc_name,
                    source="otlp",
                )
            )

    # 3. Tool call pair.
    if is_tool:
        name = _tool_name(span)
        tool_args = span.attributes.get("gen_ai.tool.input") or {}
        if isinstance(tool_args, str):
            tool_args = {"input": tool_args}
        tool_result = (
            span.attributes.get("gen_ai.tool.output")
            or span.attributes.get("gen_ai.tool.result")
            or ""
        )
        events.append(
            BeforeToolUsePayload(
                session_id=session_id,
                agent_id=agent_id,
                sequence=t.next_sequence(span.trace_id),
                tool_name=name,
                args=dict(tool_args) if isinstance(tool_args, dict) else {},
                source="otlp",
            )
        )
        events.append(
            AfterToolUsePayload(
                session_id=session_id,
                agent_id=agent_id,
                sequence=t.next_sequence(span.trace_id),
                tool_name=name,
                result=str(tool_result)[:4000],
                duration_ms=span.duration_ms,
                source="otlp",
            )
        )

    # 4. on_error on any error status.
    if span.is_error:
        events.append(
            OnErrorPayload(
                session_id=session_id,
                agent_id=agent_id,
                sequence=t.next_sequence(span.trace_id),
                error_type="otel_span_error",
                message=(span.status_message or "span reported error")[:2000],
                source="otlp",
            )
        )

    # 5. Root span close → on_final_output + on_session_end.
    if span.is_root:
        final_text = _extract_assistant_text(span)
        events.append(
            OnFinalOutputPayload(
                session_id=session_id,
                agent_id=agent_id,
                sequence=t.next_sequence(span.trace_id),
                final_response=final_text[:4000],
                total_steps=0,
                source="otlp",
            )
        )
        if t.claim_session_end(span.trace_id):
            events.append(
                OnSessionEndPayload(
                    session_id=session_id,
                    agent_id=agent_id,
                    sequence=t.next_sequence(span.trace_id),
                    total_duration_ms=span.duration_ms,
                    success=not span.is_error,
                    source="otlp",
                )
            )

    return events


__all__ = [
    "ParsedSpan",
    "TraceTracker",
    "parse_otlp_request",
    "map_genai_span_to_safer",
    "_reset_tracker_for_tests",
]
