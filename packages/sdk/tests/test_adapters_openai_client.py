"""OpenAI raw SDK adapter tests — `safer.adapters.openai_client`.

Covers the new behaviour added in Faz 35.7:
  * whitelist of LLM call paths (no phantom events from embeddings/files/etc.)
  * async support (AsyncOpenAI returns coroutines)
  * streaming accumulator (chat.completions + responses, sync + async)
  * tool_call auto-detection in non-streaming responses
  * tool_result pairing on subsequent requests → after_tool_use synth
  * `with_raw_response` LLM call instrumentation
  * cache_read tokens propagated to the dashboard
  * session_id rotation across end_session boundaries
  * provider correlation id (`response.id`) carried in `source` field
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest


# ----- recording fixture (same shape as test_adapters_misc.py) -------------


@pytest.fixture()
def recording_client(monkeypatch):
    calls: list[dict[str, Any]] = []

    class _Dummy:
        def track_event(self, hook, payload, session_id=None, agent_id=None):
            calls.append(
                {
                    "hook": hook.value if hasattr(hook, "value") else str(hook),
                    "payload": payload,
                    "session_id": session_id,
                    "agent_id": agent_id,
                }
            )

        def emit(self, event):
            payload = event.model_dump(mode="json") if hasattr(event, "model_dump") else event
            hook_val = event.hook if hasattr(event, "hook") else payload.get("hook")
            calls.append(
                {
                    "hook": hook_val.value if hasattr(hook_val, "value") else str(hook_val),
                    "payload": payload,
                    "session_id": payload.get("session_id"),
                    "agent_id": payload.get("agent_id"),
                }
            )

        def next_sequence(self, session_id):
            n = getattr(self, "_seq_counter", 0)
            self._seq_counter = n + 1
            return n

    from safer import client as client_mod

    monkeypatch.setattr(client_mod, "_client", _Dummy(), raising=False)
    return calls


# ----- helpers --------------------------------------------------------------


def _chat_response(
    text: str = "ok",
    *,
    tokens_in: int = 10,
    tokens_out: int = 5,
    cached: int = 0,
    tool_calls: list[dict] | None = None,
    response_id: str = "chatcmpl_test",
):
    msg_kwargs = {"content": text if not tool_calls else None, "tool_calls": None}
    if tool_calls:
        tcs = []
        for i, tc in enumerate(tool_calls):
            tcs.append(
                SimpleNamespace(
                    id=tc.get("id", f"call_{i}"),
                    type="function",
                    function=SimpleNamespace(
                        name=tc["name"], arguments=json.dumps(tc.get("args", {}))
                    ),
                )
            )
        msg_kwargs["tool_calls"] = tcs
    msg = SimpleNamespace(**msg_kwargs)
    choice = SimpleNamespace(
        message=msg,
        finish_reason="tool_calls" if tool_calls else "stop",
    )
    details = SimpleNamespace(cached_tokens=cached) if cached else None
    usage = SimpleNamespace(
        prompt_tokens=tokens_in,
        completion_tokens=tokens_out,
        prompt_tokens_details=details,
    )
    return SimpleNamespace(
        id=response_id,
        model="gpt-4o-mini",
        choices=[choice],
        usage=usage,
    )


def _responses_response(text: str = "ok", *, tokens_in: int = 10, tokens_out: int = 5):
    """Build a Responses-API style response object."""
    output_msg = SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="output_text", text=text)],
    )
    usage = SimpleNamespace(
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
    )
    resp = SimpleNamespace(
        id="resp_test",
        model="gpt-4o",
        output=[output_msg],
        output_text=text,
        usage=usage,
    )
    return resp


def _make_fake_openai(create_response: Any = None):
    """Build a fake OpenAI client with chat.completions, responses, and several
    other namespaces that should NOT be wrapped (embeddings, files, ...)."""

    if create_response is None:
        create_response = _chat_response()

    chat_create = lambda **kwargs: create_response
    chat = SimpleNamespace(completions=SimpleNamespace(create=chat_create))

    responses = SimpleNamespace(create=lambda **kwargs: _responses_response())

    # Non-LLM namespaces — should pass through without emitting events
    embeddings = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(data=[]))
    files = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(id="file_x"))
    images = SimpleNamespace(generate=lambda **kwargs: SimpleNamespace(data=[]))

    return SimpleNamespace(
        chat=chat,
        responses=responses,
        embeddings=embeddings,
        files=files,
        images=images,
    )


# ----- tests ---------------------------------------------------------------


def test_chat_completions_emits_real_after_llm_with_pricing(recording_client):
    """gpt-4o-mini: 10 input + 5 output → expected $0.0000045"""
    from safer.adapters.openai_client import wrap_openai

    fake = _make_fake_openai(_chat_response(text="hi", tokens_in=10, tokens_out=5))
    client = wrap_openai(fake, agent_id="cost_real")
    client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])

    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["tokens_in"] == 10
    assert after["payload"]["tokens_out"] == 5
    expected = (10 * 0.15 + 5 * 0.60) / 1_000_000
    assert abs(after["payload"]["cost_usd"] - expected) < 1e-9
    assert after["payload"]["model"] == "gpt-4o-mini"


def test_embeddings_create_does_NOT_emit_phantom_events(recording_client):
    """Critical regression: the previous adapter wrapped EVERY .create method,
    producing fake before/after_llm_call events for embeddings, file uploads,
    image generations, etc.  The new whitelist must skip them entirely."""
    from safer.adapters.openai_client import wrap_openai

    fake = _make_fake_openai()
    client = wrap_openai(fake, agent_id="no_phantom")

    # Call non-LLM endpoints
    client.embeddings.create(model="text-embedding-3-small", input="hello")
    client.files.create(file=b"x", purpose="assistants")
    client.images.generate(prompt="a cat", model="gpt-image-1")

    # No SAFER events should have fired
    hooks = [c["hook"] for c in recording_client]
    assert "before_llm_call" not in hooks, f"phantom LLM event for embeddings/files/images: {hooks}"
    assert "after_llm_call" not in hooks


def test_tool_calls_in_response_emit_decision_and_before_tool_use(recording_client):
    """When the model returns finish_reason='tool_calls', the adapter must
    auto-emit on_agent_decision + before_tool_use without manual helpers."""
    from safer.adapters.openai_client import wrap_openai

    fake = _make_fake_openai(
        _chat_response(
            tool_calls=[
                {"id": "call_a", "name": "read_file", "args": {"path": "x.md"}},
                {"id": "call_b", "name": "search", "args": {"q": "safer"}},
            ]
        )
    )
    client = wrap_openai(fake, agent_id="tool_auto")
    client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "do stuff"}]
    )

    decisions = [c for c in recording_client if c["hook"] == "on_agent_decision"]
    before_tools = [c for c in recording_client if c["hook"] == "before_tool_use"]
    assert len(decisions) == 2
    assert len(before_tools) == 2
    names = sorted(d["payload"]["chosen_action"].split("(")[0] for d in decisions)
    assert names == ["read_file", "search"]
    # Tool args parsed from JSON string
    tool_a = next(c for c in before_tools if c["payload"]["tool_name"] == "read_file")
    assert tool_a["payload"]["args"] == {"path": "x.md"}


def test_tool_result_in_next_request_pairs_after_tool_use(recording_client):
    """Adapter must pair tool_result messages on the next chat.completions
    request with the pending tool_calls and emit after_tool_use."""
    from safer.adapters.openai_client import wrap_openai

    response_with_tool = _chat_response(
        tool_calls=[{"id": "call_xyz", "name": "lookup", "args": {"q": "k"}}]
    )

    # First call returns tool_calls
    fake = _make_fake_openai(response_with_tool)
    client = wrap_openai(fake, agent_id="pair_synth")
    client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "go"}]
    )

    # Swap response: model concludes
    fake.chat.completions.create = lambda **kwargs: _chat_response(text="all done")

    # User sends tool_result back
    client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_xyz",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_xyz", "content": "result content"},
        ],
    )

    after = [c for c in recording_client if c["hook"] == "after_tool_use"]
    assert len(after) == 1
    assert after[0]["payload"]["tool_name"] == "lookup"
    assert "result content" in after[0]["payload"]["result"]


def test_responses_api_extracts_text_via_output_text(recording_client):
    """The Responses API uses Pydantic models for content blocks, NOT dicts.
    Older code's `isinstance(c, dict)` check always returned False, so
    extracted text was always empty.  We now use `response.output_text`."""
    from safer.adapters.openai_client import wrap_openai

    fake = _make_fake_openai()
    client = wrap_openai(fake, agent_id="responses_text")
    client.responses.create(model="gpt-4o", input="say hi")

    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["response"] == "ok"


def test_async_openai_actually_awaits(recording_client):
    """Critical regression: AsyncOpenAI.chat.completions.create is `async def`
    so calling it without `await` returns a coroutine.  The adapter must
    detect this and produce an async wrapped fn so `await client.chat...
    .create()` returns the real ChatCompletion (not garbage)."""
    from safer.adapters.openai_client import wrap_openai

    async def async_create(**kwargs):
        return _chat_response(text="async response", tokens_in=11, tokens_out=3)

    fake = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=async_create))
    )
    client = wrap_openai(fake, agent_id="async_real")

    async def run():
        return await client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )

    result = asyncio.run(run())
    assert result.choices[0].message.content == "async response"

    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["tokens_in"] == 11
    assert after["payload"]["tokens_out"] == 3
    assert after["payload"]["response"] == "async response"


def test_streaming_chat_accumulates_text_and_usage(recording_client):
    """Sync streaming: every chunk goes through the wrapper's accumulator;
    `after_llm_call` fires at stream end with real text + usage from the
    final chunk's `usage` field (auto-injected via stream_options)."""
    from safer.adapters.openai_client import wrap_openai

    chunks = [
        SimpleNamespace(
            id="chatcmpl_stream",
            model="gpt-4o-mini",
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="Hello ", tool_calls=None),
                    finish_reason=None,
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            id="chatcmpl_stream",
            model="gpt-4o-mini",
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="world!", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        ),
        # Final chunk with usage (only present when stream_options.include_usage=True)
        SimpleNamespace(
            id="chatcmpl_stream",
            model="gpt-4o-mini",
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=8,
                completion_tokens=4,
                prompt_tokens_details=None,
            ),
        ),
    ]

    def stream_create(**kwargs):
        # The adapter should auto-inject stream_options.include_usage
        assert kwargs.get("stream") is True
        assert kwargs.get("stream_options", {}).get("include_usage") is True
        return iter(chunks)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=stream_create)))
    client = wrap_openai(fake, agent_id="stream_chat")

    stream = client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], stream=True
    )
    consumed = list(stream)
    assert len(consumed) == 3

    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["response"] == "Hello world!"
    assert after["payload"]["tokens_in"] == 8
    assert after["payload"]["tokens_out"] == 4


def test_streaming_chat_user_explicit_include_usage_false_is_respected(recording_client):
    """If the user explicitly passed stream_options={"include_usage": False},
    we MUST NOT override their choice."""
    from safer.adapters.openai_client import wrap_openai

    captured_kwargs: list[dict] = []

    def stream_create(**kwargs):
        captured_kwargs.append(kwargs)
        return iter([])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=stream_create)))
    client = wrap_openai(fake, agent_id="stream_no_usage")
    list(
        client.chat.completions.create(
            model="gpt-4o",
            messages=[],
            stream=True,
            stream_options={"include_usage": False},
        )
    )

    assert captured_kwargs[0]["stream_options"] == {"include_usage": False}


def test_streaming_chat_accumulates_tool_call_arguments(recording_client):
    """Tool calls stream chunk-by-chunk: each chunk has a partial JSON string
    in delta.tool_calls[i].function.arguments.  We concat across chunks
    indexed by `.index`, parse JSON at end, and emit before_tool_use."""
    from safer.adapters.openai_client import wrap_openai

    chunks = [
        # First chunk: tool_call id + name
        SimpleNamespace(
            id="cs", model="gpt-4o",
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[SimpleNamespace(
                        index=0, id="call_abc", type="function",
                        function=SimpleNamespace(name="lookup", arguments='{"q":')
                    )]
                ),
                finish_reason=None,
            )],
            usage=None,
        ),
        # Second chunk: rest of the JSON arguments
        SimpleNamespace(
            id="cs", model="gpt-4o",
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[SimpleNamespace(
                        index=0, id=None, type=None,
                        function=SimpleNamespace(name=None, arguments=' "safer"}')
                    )]
                ),
                finish_reason="tool_calls",
            )],
            usage=None,
        ),
        # Final usage chunk
        SimpleNamespace(
            id="cs", model="gpt-4o",
            choices=[],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5, prompt_tokens_details=None),
        ),
    ]

    fake = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: iter(chunks)))
    )
    client = wrap_openai(fake, agent_id="stream_tool")
    stream = client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "go"}], stream=True
    )
    list(stream)

    before_tools = [c for c in recording_client if c["hook"] == "before_tool_use"]
    assert len(before_tools) == 1
    assert before_tools[0]["payload"]["tool_name"] == "lookup"
    assert before_tools[0]["payload"]["args"] == {"q": "safer"}


def test_with_raw_response_emits_event_pair(recording_client):
    """Production OpenAI users frequently use `with_raw_response.create` to
    get HTTP headers (x-request-id, ratelimit info).  The adapter must
    wrap this path too — earlier versions silently produced no events."""
    from safer.adapters.openai_client import wrap_openai

    parsed = _chat_response(text="parsed result", tokens_in=20, tokens_out=10)

    class _RawResponse:
        def __init__(self, parsed):
            self._parsed = parsed
            self.headers = {"x-request-id": "req_correlation_id"}
        def parse(self):
            return self._parsed

    raw_create = lambda **kwargs: _RawResponse(parsed)
    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=raw_create),
                create=lambda **kw: parsed,
            )
        )
    )
    client = wrap_openai(fake, agent_id="raw_response")
    raw = client.chat.completions.with_raw_response.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
    )
    assert raw.headers["x-request-id"] == "req_correlation_id"

    hooks = [c["hook"] for c in recording_client]
    assert "before_llm_call" in hooks
    assert "after_llm_call" in hooks
    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["tokens_in"] == 20
    assert after["payload"]["response"] == "parsed result"


def test_session_id_rotates_after_end_session(recording_client):
    """Calling end_session() then making a new create call should produce
    a fresh SAFER session_id — not reuse the closed one."""
    from safer.adapters.openai_client import wrap_openai

    fake = _make_fake_openai()
    client = wrap_openai(fake, agent_id="rotate_oc")

    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    sid_1 = client.session_id
    client.end_session()

    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    sid_2 = client.session_id

    assert sid_1 != sid_2


def test_response_id_is_carried_in_source(recording_client):
    """The provider's response.id correlates SAFER events with OpenAI's
    own dashboard.  We surface it via the event's `source` field."""
    from safer.adapters.openai_client import wrap_openai

    fake = _make_fake_openai(_chat_response(response_id="chatcmpl_xyz"))
    client = wrap_openai(fake, agent_id="corr_id")
    client.chat.completions.create(model="gpt-4o-mini", messages=[])

    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert "chatcmpl_xyz" in after["payload"]["source"]


def test_cache_read_tokens_propagated(recording_client):
    """gpt-4o supports prompt caching; cached_tokens lives in
    prompt_tokens_details.cached_tokens.  The adapter must surface this
    so the dashboard shows the cache-hit ratio."""
    from safer.adapters.openai_client import wrap_openai

    fake = _make_fake_openai(_chat_response(tokens_in=100, tokens_out=20, cached=80))
    client = wrap_openai(fake, agent_id="cache_test")
    client.chat.completions.create(model="gpt-4o-mini", messages=[])

    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["cache_read_tokens"] == 80
    # Billable input = 100 - 80 = 20; cost = (20 * 0.15 + 80 * 0.075 + 20 * 0.60) / 1M
    expected = (20 * 0.15 + 80 * 0.075 + 20 * 0.60) / 1_000_000
    assert abs(after["payload"]["cost_usd"] - expected) < 1e-9


# ----- with_streaming_response wrapping (35.7m) ----------------------------


class _FakeAPIResponseStream:
    """Minimal stand-in for OpenAI's APIResponse returned by
    with_streaming_response.  Provides iter_lines that yields SSE-format lines."""

    def __init__(self, sse_chunks: list[dict], headers: dict | None = None):
        # Each chunk in sse_chunks is a dict matching ChatCompletionChunk shape;
        # we serialize as `data: {json}\n` per OpenAI SSE convention, plus a
        # trailing `data: [DONE]`.
        self._sse_chunks = sse_chunks
        self.headers = headers or {"x-request-id": "req_stream_test"}
        self.status_code = 200

    def iter_lines(self):
        for chunk in self._sse_chunks:
            yield f"data: {json.dumps(chunk)}"
            yield ""  # blank line separator (per SSE spec)
        yield "data: [DONE]"


class _FakeStreamingResponseManager:
    """Stand-in for the context manager returned by
    `with_streaming_response.create()`."""

    def __init__(self, response: _FakeAPIResponseStream):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, exc_type, exc, tb):
        return None


def test_with_streaming_response_accumulates_chunks_from_iter_lines(recording_client):
    """`with_streaming_response.create(stream=True)` returns a context
    manager whose APIResponse exposes `iter_lines()` (SSE).  The adapter
    must intercept iteration, accumulate chunks, and emit a real
    `after_llm_call` on context exit — not pass through silently."""
    from safer.adapters.openai_client import wrap_openai

    sse_chunks = [
        {
            "id": "chatcmpl_ws",
            "model": "gpt-4o",
            "choices": [
                {"delta": {"content": "Hello "}, "finish_reason": None}
            ],
            "usage": None,
        },
        {
            "id": "chatcmpl_ws",
            "model": "gpt-4o",
            "choices": [
                {"delta": {"content": "stream!"}, "finish_reason": "stop"}
            ],
            "usage": None,
        },
        {
            "id": "chatcmpl_ws",
            "model": "gpt-4o",
            "choices": [],
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "prompt_tokens_details": None,
            },
        },
    ]

    api_response = _FakeAPIResponseStream(sse_chunks)
    manager = _FakeStreamingResponseManager(api_response)

    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_streaming_response=SimpleNamespace(create=lambda **kw: manager),
                create=lambda **kw: None,
            )
        )
    )
    client = wrap_openai(fake, agent_id="ws_iter_lines")

    consumed_lines: list[str] = []
    with client.chat.completions.with_streaming_response.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}], stream=True
    ) as response:
        # Headers + status should still be accessible (proxy passthrough)
        assert response.headers["x-request-id"] == "req_stream_test"
        assert response.status_code == 200
        for line in response.iter_lines():
            consumed_lines.append(line)

    # User received raw SSE lines untouched
    assert any("Hello" in l for l in consumed_lines)
    assert any("[DONE]" in l for l in consumed_lines)

    # SAFER emitted before + after with assembled text + usage
    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["response"] == "Hello stream!"
    assert after["payload"]["tokens_in"] == 7
    assert after["payload"]["tokens_out"] == 3
    expected_cost = (7 * 2.50 + 3 * 10.0) / 1_000_000
    assert abs(after["payload"]["cost_usd"] - expected_cost) < 1e-9


def test_with_streaming_response_iter_text_buffers_partial_lines(recording_client):
    """`iter_text` may split a single SSE event mid-line.  The proxy must
    buffer across chunks and feed complete lines to the accumulator."""
    from safer.adapters.openai_client import wrap_openai

    chunk_dict = {
        "id": "cs",
        "model": "gpt-4o-mini",
        "choices": [{"delta": {"content": "buffered"}, "finish_reason": "stop"}],
        "usage": None,
    }

    class _PartialAPIResponse:
        headers = {"x-request-id": "buf"}
        status_code = 200
        def iter_text(self):
            full = f"data: {json.dumps(chunk_dict)}\ndata: [DONE]\n"
            # Split mid-byte pattern to exercise the buffer
            yield full[:10]
            yield full[10:30]
            yield full[30:]

    class _Manager:
        def __enter__(self): return _PartialAPIResponse()
        def __exit__(self, *_): return None

    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_streaming_response=SimpleNamespace(create=lambda **kw: _Manager()),
                create=lambda **kw: None,
            )
        )
    )
    client = wrap_openai(fake, agent_id="ws_iter_text")
    received = []
    with client.chat.completions.with_streaming_response.create(
        model="gpt-4o-mini", messages=[], stream=True
    ) as response:
        for chunk in response.iter_text():
            received.append(chunk)

    # User saw the original byte chunks
    assert "".join(received).startswith("data: ")

    # SAFER reconstructed the SSE event via the buffer
    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["response"] == "buffered"


def test_with_streaming_response_async_context_manager(recording_client):
    """async with client.chat.completions.with_streaming_response.create(...)"""
    from safer.adapters.openai_client import wrap_openai

    sse_chunks = [
        {
            "id": "cs_async",
            "model": "gpt-4o",
            "choices": [{"delta": {"content": "async"}, "finish_reason": "stop"}],
            "usage": None,
        },
        {
            "id": "cs_async",
            "model": "gpt-4o",
            "choices": [],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "prompt_tokens_details": None},
        },
    ]

    class _AsyncAPIResponse:
        headers = {"x-request-id": "async_req"}
        status_code = 200
        async def iter_lines(self):
            for ch in sse_chunks:
                yield f"data: {json.dumps(ch)}"
            yield "data: [DONE]"

    class _AsyncManager:
        async def __aenter__(self):
            return _AsyncAPIResponse()
        async def __aexit__(self, *_):
            return None

    async def async_create(**kwargs):
        return _AsyncManager()

    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_streaming_response=SimpleNamespace(create=async_create),
                create=lambda **kw: None,
            )
        )
    )
    client = wrap_openai(fake, agent_id="ws_async")

    async def run():
        manager = await client.chat.completions.with_streaming_response.create(
            model="gpt-4o", messages=[], stream=True
        )
        consumed = []
        async with manager as response:
            assert response.headers["x-request-id"] == "async_req"
            async for line in response.aiter_lines():
                consumed.append(line)
        return consumed

    consumed = asyncio.run(run())
    assert any("async" in l for l in consumed)

    after = next(c for c in recording_client if c["hook"] == "after_llm_call")
    assert after["payload"]["response"] == "async"
    assert after["payload"]["tokens_in"] == 5
    assert after["payload"]["tokens_out"] == 1


def test_with_streaming_response_context_manager_emits_after_on_exit(recording_client):
    """Regression: earlier the wrap path returned the manager unmodified
    and emitted nothing.  Confirm both before_llm_call and after_llm_call
    fire across the with block, and after_llm_call carries real metadata."""
    from safer.adapters.openai_client import wrap_openai

    sse_chunks = [
        {
            "id": "x",
            "model": "gpt-4o-mini",
            "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": None,
        },
        {
            "id": "x",
            "model": "gpt-4o-mini",
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "prompt_tokens_details": None},
        },
    ]
    api_response = _FakeAPIResponseStream(sse_chunks)
    manager = _FakeStreamingResponseManager(api_response)

    fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_streaming_response=SimpleNamespace(create=lambda **kw: manager),
                create=lambda **kw: None,
            )
        )
    )
    client = wrap_openai(fake, agent_id="ws_event_pair")

    with client.chat.completions.with_streaming_response.create(
        model="gpt-4o-mini", messages=[], stream=True
    ) as response:
        list(response.iter_lines())  # consume

    hooks = [c["hook"] for c in recording_client]
    assert hooks.count("before_llm_call") == 1
    assert hooks.count("after_llm_call") == 1
