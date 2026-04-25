"""End-to-end test for the OTel bridge adapter.

Drives a real `anthropic.Anthropic` client whose calls are
auto-instrumented by the `opentelemetry-instrumentation-anthropic`
package; the spans that fall out are fed straight through SAFER's
backend OTLP parser to produce SAFER lifecycle events.

The only SAFER-specific lines in user code are the README's two:

    from safer.adapters.otel import configure_otel_bridge
    configure_otel_bridge(agent_id=..., instrument=["anthropic"])
    client = Anthropic()  # plain client; the instrumentor patches it
    client.messages.create(...)

The test substitutes the bridge's default OTLP-over-HTTP exporter
with an `InMemorySpanExporter` so we don't need a running backend; the
captured spans are then run through `safer_backend.ingestion.otlp` —
the exact code path a real `/v1/traces` POST would hit — and the
emitted SAFER events are verified.

Intentional weakness in the demo: the model returns a tool_use block
that the OTel bridge will surface as `before_tool_use` only when the
chat span carries `gen_ai.tool.call.id`.  This test exercises that
synthesis path explicitly.
"""

from __future__ import annotations

import json

import httpx
import pytest


pytest.importorskip("opentelemetry.instrumentation.anthropic")


def _msg_body(*, content: list[dict], stop_reason: str = "end_turn"):
    return {
        "id": "msg_otel_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 22,
            "output_tokens": 14,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def _anthropic_mock_handler(request: httpx.Request) -> httpx.Response:
    # Return a tool_use response so the chat span carries
    # `gen_ai.tool.call.id` (which is what triggers SAFER's tool synth
    # on the OTel-bridge path).
    return httpx.Response(
        200,
        json=_msg_body(
            content=[
                {"type": "text", "text": "Looking up the customer..."},
                {
                    "type": "tool_use",
                    "id": "toolu_01x",
                    "name": "lookup_customer",
                    "input": {"customer_id": "C-1024"},
                },
            ],
            stop_reason="tool_use",
        ),
    )


def test_otel_bridge_captures_anthropic_span_and_parses_to_safer_events():
    from anthropic import Anthropic
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    # --- README-pattern integration ------------------------------------
    from safer.adapters.otel import _reset_for_tests, configure_otel_bridge

    # Make sure prior tests didn't leave the bridge configured pointing
    # at a stale endpoint.  Fresh setup per test.
    _reset_for_tests()
    configure_otel_bridge(
        agent_id="customer_support",
        agent_name="Customer Support",
        # Endpoint won't matter — we attach an in-memory exporter alongside
        # to capture the spans for verification.
        endpoint="http://127.0.0.1:1/v1/traces",
        instrument=["anthropic"],
    )
    # ------------------------------------------------------------------

    # `configure_otel_bridge` installs a TracerProvider with an
    # OTLP-over-HTTP exporter pointing at SAFER's `/v1/traces`.  In a
    # production deployment that connects to the SAFER backend on
    # `localhost:8000`.  Here we attach an additional in-memory
    # exporter so we can inspect what the OpenLLMetry Anthropic
    # instrumentor produced (the OTLP exporter's connection errors are
    # benign noise — its spans are dropped silently).
    captured_exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    provider.add_span_processor(SimpleSpanProcessor(captured_exporter))

    # User-side code: plain Anthropic client
    transport = httpx.MockTransport(_anthropic_mock_handler)
    client = Anthropic(
        api_key="sk-test", http_client=httpx.Client(transport=transport)
    )
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=128,
        tools=[
            {
                "name": "lookup_customer",
                "description": "Fetch a customer record",
                "input_schema": {
                    "type": "object",
                    "properties": {"customer_id": {"type": "string"}},
                    "required": ["customer_id"],
                },
            }
        ],
        messages=[{"role": "user", "content": "Look up customer C-1024"}],
    )
    assert response.stop_reason == "tool_use"

    # OpenLLMetry's BatchSpanProcessor flushes asynchronously; ours uses
    # SimpleSpanProcessor so spans are exported synchronously.  Pull them.
    spans = captured_exporter.get_finished_spans()
    assert spans, (
        "expected the OpenLLMetry anthropic instrumentor to emit at least "
        "one span — got none.  Check that configure_otel_bridge ran and "
        "that the instrumentor is installed."
    )

    # The chat span will carry gen_ai.* attributes.
    chat_span = next(
        (s for s in spans if "anthropic" in s.attributes.get("gen_ai.system", "")),
        spans[0],
    )

    # Hand the spans off to the SAFER backend OTLP parser — same code
    # path a real `/v1/traces` POST hits.
    from opentelemetry.exporter.otlp.proto.common._internal.trace_encoder import (
        encode_spans,
    )

    from safer_backend.ingestion.otlp import (
        _reset_tracker_for_tests,
        map_genai_span_to_safer,
        parse_otlp_request,
    )

    _reset_tracker_for_tests()
    body = encode_spans(spans).SerializeToString()
    parsed_spans = parse_otlp_request(body, "application/x-protobuf")
    safer_events: list = []
    for ps in parsed_spans:
        safer_events.extend(map_genai_span_to_safer(ps))

    hook_names = [e.hook.value for e in safer_events]

    # SAFER session opened on the first span of this trace
    assert "on_session_start" in hook_names
    # The chat span produced before/after_llm_call
    assert "before_llm_call" in hook_names
    assert "after_llm_call" in hook_names

    # The tool_use response carried `gen_ai.tool.call.id` — SAFER
    # synthesised an on_agent_decision (and a before_tool_use) for it.
    assert "on_agent_decision" in hook_names, (
        f"expected tool synth from gen_ai.tool.call.id; got hooks={hook_names}; "
        f"chat span attrs={dict(chat_span.attributes)}"
    )

    # Cost via the shared pricing table — Haiku 4.5 prompt+output > 0
    after = next(e for e in safer_events if e.hook.value == "after_llm_call")
    assert after.tokens_in > 0
    assert after.tokens_out > 0
    assert after.cost_usd > 0
