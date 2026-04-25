"""OTLP GenAI ingestion tests.

Every test builds **real OpenTelemetry spans** via the official SDK,
serializes them into `ExportTraceServiceRequest` protobuf bytes, and
feeds the bytes through `parse_otlp_request` + `map_genai_span_to_safer`.
This is as close to live behaviour as a unit test gets without booting
a real Anthropic / OpenAI client.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.protobuf import json_format
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.common._internal.trace_encoder import (
    encode_spans,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from safer_backend.ingestion.otlp import (
    _reset_tracker_for_tests,
    map_genai_span_to_safer,
    parse_otlp_request,
)
from safer_backend.main import app


@pytest.fixture(autouse=True)
def _reset_tracker_between_tests():
    _reset_tracker_for_tests()
    yield
    _reset_tracker_for_tests()


# ----- span-building helpers ------------------------------------------


def _fresh_exporter() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Build a TracerProvider that captures every span in memory.

    We do NOT install this provider globally — we call `tracer` via the
    provider directly so concurrent tests don't collide.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource.create({"service.name": "test_agent"})
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _serialize(exporter: InMemorySpanExporter) -> bytes:
    spans = exporter.get_finished_spans()
    assert spans, "no spans to serialize"
    req = encode_spans(spans)
    return req.SerializeToString()


def _serialize_json(exporter: InMemorySpanExporter) -> bytes:
    spans = exporter.get_finished_spans()
    req = encode_spans(spans)
    return json_format.MessageToJson(req).encode("utf-8")


# ----- tests ----------------------------------------------------------


def test_chat_span_parses_into_llm_call_pair():
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("anthropic-test")
    with tracer.start_as_current_span("chat claude-opus-4-7") as span:
        span.set_attribute("gen_ai.system", "anthropic")
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", "claude-opus-4-7")
        span.set_attribute("gen_ai.response.model", "claude-opus-4-7")
        span.set_attribute("gen_ai.usage.input_tokens", 120)
        span.set_attribute("gen_ai.usage.output_tokens", 45)
        span.add_event("gen_ai.user.message", {"content": "Hello"})
        span.add_event("gen_ai.assistant.message", {"content": "Hi there"})

    body = _serialize(exporter)
    spans = parse_otlp_request(body, "application/x-protobuf")
    assert len(spans) == 1
    events = map_genai_span_to_safer(spans[0])
    hooks = [e.hook.value for e in events]

    # Root span with chat op → session_start + before/after_llm_call +
    # final_output + session_end.
    assert "on_session_start" in hooks
    assert "before_llm_call" in hooks
    assert "after_llm_call" in hooks
    assert "on_final_output" in hooks
    assert "on_session_end" in hooks

    after_llm = next(e for e in events if e.hook.value == "after_llm_call")
    assert after_llm.tokens_in == 120
    assert after_llm.tokens_out == 45
    assert after_llm.model == "claude-opus-4-7"
    assert "Hi there" in after_llm.response


def test_tool_span_parses_into_tool_use_pair():
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("openai-test")
    # Parent chat span to make the tool span non-root
    with tracer.start_as_current_span("chat gpt-4o") as parent:
        parent.set_attribute("gen_ai.operation.name", "chat")
        parent.set_attribute("gen_ai.request.model", "gpt-4o")
        with tracer.start_as_current_span("execute_tool search") as ts:
            ts.set_attribute("gen_ai.operation.name", "execute_tool")
            ts.set_attribute("gen_ai.tool.name", "search")
            ts.set_attribute("gen_ai.tool.call.id", "call_123")
            ts.set_attribute(
                "gen_ai.tool.output", "found 3 results"
            )

    body = _serialize(exporter)
    spans = parse_otlp_request(body, "application/x-protobuf")
    # Flatten events from every span.
    all_events = []
    for s in spans:
        all_events.extend(map_genai_span_to_safer(s))
    hooks = [e.hook.value for e in all_events]

    assert "before_tool_use" in hooks
    assert "after_tool_use" in hooks
    tool_start = next(e for e in all_events if e.hook.value == "before_tool_use")
    assert tool_start.tool_name == "search"


def test_error_status_span_emits_on_error():
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("err-test")
    with tracer.start_as_current_span("chat claude-opus-4-7") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", "claude-opus-4-7")
        span.set_status(Status(StatusCode.ERROR, "rate limited"))

    body = _serialize(exporter)
    spans = parse_otlp_request(body, "application/x-protobuf")
    events = map_genai_span_to_safer(spans[0])
    hooks = [e.hook.value for e in events]
    assert "on_error" in hooks
    err_ev = next(e for e in events if e.hook.value == "on_error")
    assert "rate limited" in err_ev.message


def test_json_body_also_parses():
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("json-test")
    with tracer.start_as_current_span("chat gpt-4o") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", "gpt-4o")
        span.set_attribute("gen_ai.usage.input_tokens", 10)
        span.set_attribute("gen_ai.usage.output_tokens", 5)

    body = _serialize_json(exporter)
    spans = parse_otlp_request(body, "application/json")
    assert len(spans) == 1
    events = map_genai_span_to_safer(spans[0])
    hook_names = [e.hook.value for e in events]
    assert "before_llm_call" in hook_names
    assert "after_llm_call" in hook_names


def test_malformed_body_raises_value_error():
    with pytest.raises(ValueError, match="malformed OTLP payload"):
        parse_otlp_request(b"\x00not a real proto\x00", "application/x-protobuf")


def test_session_synthesis_dedupes_across_batches():
    """Two successive batches of spans on the same trace must produce
    exactly one on_session_start and one on_session_end."""
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("dedup-test")

    # Batch 1: child span only (non-root)
    with tracer.start_as_current_span("chat claude-opus-4-7") as root:
        root.set_attribute("gen_ai.operation.name", "chat")
        with tracer.start_as_current_span("execute_tool lookup") as tool:
            tool.set_attribute("gen_ai.operation.name", "execute_tool")
            tool.set_attribute("gen_ai.tool.name", "lookup")

    all_events = []
    for s in parse_otlp_request(_serialize(exporter), "application/x-protobuf"):
        all_events.extend(map_genai_span_to_safer(s))
    hooks = [e.hook.value for e in all_events]

    session_starts = [h for h in hooks if h == "on_session_start"]
    session_ends = [h for h in hooks if h == "on_session_end"]
    assert len(session_starts) == 1, (
        f"expected exactly one on_session_start, got {len(session_starts)}"
    )
    assert len(session_ends) == 1, (
        f"expected exactly one on_session_end, got {len(session_ends)}"
    )


def test_http_endpoint_accepts_real_otlp_payload():
    """Full E2E through FastAPI: build spans, POST to /v1/traces, assert
    the handler parses and reports accepted_spans > 0."""
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("http-e2e")
    with tracer.start_as_current_span("chat claude-opus-4-7") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", "claude-opus-4-7")
        span.set_attribute("gen_ai.usage.input_tokens", 50)
        span.set_attribute("gen_ai.usage.output_tokens", 12)

    body = _serialize(exporter)
    client = TestClient(app)
    resp = client.post(
        "/v1/traces",
        content=body,
        headers={"content-type": "application/x-protobuf"},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["accepted_spans"] >= 1
    assert out["emitted_events"] >= 2  # at least before + after_llm_call


def test_http_endpoint_rejects_malformed_body():
    client = TestClient(app)
    resp = client.post(
        "/v1/traces",
        content=b"totally not proto",
        headers={"content-type": "application/x-protobuf"},
    )
    assert resp.status_code == 400


def test_chat_span_cost_enriched_via_shared_pricing_table():
    """Regression: earlier the OTLP parser hardcoded cost_usd=0 on every
    chat span.  Now it consults `safer._pricing.estimate_cost`."""
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("cost-test")
    with tracer.start_as_current_span("chat claude-haiku-4-5") as span:
        span.set_attribute("gen_ai.system", "anthropic")
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", "claude-haiku-4-5")
        span.set_attribute("gen_ai.response.model", "claude-haiku-4-5")
        span.set_attribute("gen_ai.usage.input_tokens", 1000)
        span.set_attribute("gen_ai.usage.output_tokens", 500)

    spans = parse_otlp_request(_serialize(exporter), "application/x-protobuf")
    events = map_genai_span_to_safer(spans[0])
    after = next(e for e in events if e.hook.value == "after_llm_call")
    # Haiku 4.5: $1 input + $5 output per 1M
    expected = (1000 * 1.0 + 500 * 5.0) / 1_000_000
    assert abs(after.cost_usd - expected) < 1e-9


def test_chat_span_with_tool_call_id_synthesizes_agent_decision():
    """When the OTel anthropic instrumentor includes `gen_ai.tool.call.id`
    on a chat span (signalling that the model returned a tool_use block),
    SAFER must synthesize an `on_agent_decision` event so the Multi-Persona
    Judge's decision-hook personas (Scope, Policy Warden) can run."""
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("decision-synth")
    with tracer.start_as_current_span("chat claude-opus-4-7") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", "claude-opus-4-7")
        span.set_attribute("gen_ai.tool.call.id", "tool_use_xyz")
        span.set_attribute("gen_ai.tool.call.name", "read_file")
        span.set_attribute("gen_ai.usage.input_tokens", 50)
        span.set_attribute("gen_ai.usage.output_tokens", 12)

    spans = parse_otlp_request(_serialize(exporter), "application/x-protobuf")
    events = map_genai_span_to_safer(spans[0])
    hook_names = [e.hook.value for e in events]
    assert "on_agent_decision" in hook_names
    decision = next(e for e in events if e.hook.value == "on_agent_decision")
    assert decision.decision_type == "tool_call"
    assert decision.chosen_action == "read_file"


def test_chat_span_without_tool_call_id_does_NOT_synthesize_decision():
    """Conservative: only emit a synthesized agent_decision when the
    chat span explicitly carries `gen_ai.tool.call.id`.  Avoids false
    positives on plain text completions."""
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("no-synth")
    with tracer.start_as_current_span("chat gpt-4o") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", "gpt-4o")
        span.set_attribute("gen_ai.usage.input_tokens", 10)
        span.set_attribute("gen_ai.usage.output_tokens", 5)

    spans = parse_otlp_request(_serialize(exporter), "application/x-protobuf")
    events = map_genai_span_to_safer(spans[0])
    hook_names = [e.hook.value for e in events]
    assert "on_agent_decision" not in hook_names


def test_cache_read_token_alias_picks_up_newer_attribute_keys():
    """Newer GenAI semconv versions may use `gen_ai.usage.cache_read_tokens`
    or `gen_ai.prompt_cache.cached_input_tokens`.  We accept all aliases."""
    provider, exporter = _fresh_exporter()
    tracer = provider.get_tracer("cache-aliases")
    with tracer.start_as_current_span("chat claude-sonnet-4-6") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", "claude-sonnet-4-6")
        span.set_attribute("gen_ai.usage.input_tokens", 1000)
        span.set_attribute("gen_ai.usage.output_tokens", 100)
        # Newer alias — older parser missed this entirely
        span.set_attribute("gen_ai.usage.cache_read_tokens", 600)

    spans = parse_otlp_request(_serialize(exporter), "application/x-protobuf")
    events = map_genai_span_to_safer(spans[0])
    after = next(e for e in events if e.hook.value == "after_llm_call")
    assert after.cache_read_tokens == 600
    # Cost reflects the cache_read discount: billable = 1000 - 600 = 400
    expected = (400 * 3.0 + 600 * 0.30 + 100 * 15.0) / 1_000_000
    assert abs(after.cost_usd - expected) < 1e-9
