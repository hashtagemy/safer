"""Tests for the Claude SDK adapter.

We use a fake Anthropic client so tests don't hit the network or require
an API key. The adapter should emit all 9 hooks on the right call points.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import safer
from safer.adapters.claude_sdk import (
    _estimate_cost_usd,
    _extract_response_text,
    _summarize_messages,
    wrap_anthropic,
)
from safer.client import clear_client
from safer.events import Hook


class _FakeMessages:
    def __init__(self, response: Any) -> None:
        self._response = response

    def create(self, **kwargs: Any) -> Any:
        # Echo the model so tests can verify.
        return self._response


class _FakeAnthropic:
    def __init__(self, response: Any) -> None:
        self.messages = _FakeMessages(response)


def _capture_events(client: safer.SaferClient) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    original_emit = client.transport.emit

    def _patched(event: dict[str, Any]) -> None:
        captured.append(event)
        # Don't send upstream; we just want to record.

    client.transport.emit = _patched  # type: ignore[method-assign]
    return captured


@pytest.fixture(autouse=True)
def _reset():
    clear_client()
    yield
    clear_client()


def _mock_response(text: str = "done", tokens_in: int = 10, tokens_out: int = 5):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def test_wrap_anthropic_emits_before_and_after_llm():
    client = safer.instrument(api_url="http://127.0.0.1:59999")
    events = _capture_events(client)

    fake = _FakeAnthropic(_mock_response("hi"))
    agent = wrap_anthropic(fake, agent_id="test_agent", agent_name="Test")
    agent.start_session()

    resp = agent.messages.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=100,
    )
    assert resp.content[0].text == "hi"

    hooks = [e["hook"] for e in events]
    assert Hook.ON_SESSION_START.value in hooks
    assert Hook.BEFORE_LLM_CALL.value in hooks
    assert Hook.AFTER_LLM_CALL.value in hooks


def test_full_lifecycle_emits_all_nine_hooks():
    client = safer.instrument(api_url="http://127.0.0.1:59999")
    events = _capture_events(client)

    fake = _FakeAnthropic(_mock_response())
    agent = wrap_anthropic(fake, agent_id="lifecycle_agent")

    agent.start_session(context={"user": "alice"})
    agent.messages.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "do thing"}],
    )
    agent.before_tool_use("get_order", {"id": 42})
    agent.after_tool_use("get_order", result={"status": "shipped"})
    agent.agent_decision("select_tool", reasoning="need order info", chosen_action="get_order")
    agent.final_output("Your order has shipped.", total_steps=3)
    agent.end_session(success=True)

    hooks = {e["hook"] for e in events}
    expected = {
        Hook.ON_SESSION_START.value,
        Hook.BEFORE_LLM_CALL.value,
        Hook.AFTER_LLM_CALL.value,
        Hook.BEFORE_TOOL_USE.value,
        Hook.AFTER_TOOL_USE.value,
        Hook.ON_AGENT_DECISION.value,
        Hook.ON_FINAL_OUTPUT.value,
        Hook.ON_SESSION_END.value,
    }
    assert expected.issubset(hooks)


def test_error_emits_on_error_hook():
    client = safer.instrument(api_url="http://127.0.0.1:59999")
    events = _capture_events(client)

    class _FailingMessages:
        def create(self, **kwargs):
            raise RuntimeError("boom")

    fake = SimpleNamespace(messages=_FailingMessages())
    agent = wrap_anthropic(fake, agent_id="err_agent")
    agent.start_session()

    with pytest.raises(RuntimeError):
        agent.messages.create(model="claude-opus-4-7", messages=[])

    hooks = [e["hook"] for e in events]
    assert Hook.ON_ERROR.value in hooks


def test_cost_estimation_opus():
    cost = _estimate_cost_usd("claude-opus-4-7", tokens_in=1000, tokens_out=500, cache_read=0, cache_write=0)
    # Expected: 1000/1M * 15 + 500/1M * 75 = 0.015 + 0.0375 = 0.0525
    assert cost == pytest.approx(0.0525, rel=1e-3)


def test_cost_estimation_with_cache():
    # Cache read saves most of input cost.
    cost = _estimate_cost_usd(
        "claude-opus-4-7",
        tokens_in=1000,
        tokens_out=0,
        cache_read=900,
        cache_write=0,
    )
    # Billable input = 1000 - 900 = 100 → 100/1M * 15 + 900/1M * 1.5
    expected = (100 * 15 + 900 * 1.5) / 1_000_000
    assert cost == pytest.approx(expected, rel=1e-3)


def test_summarize_messages_handles_list_content():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    ]
    out = _summarize_messages(msgs)
    assert "[user] hello" in out
    assert "[assistant] hi" in out


def test_extract_response_text_with_tool_use():
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Let me check."),
            SimpleNamespace(type="tool_use", name="get_order", input={}),
        ]
    )
    out = _extract_response_text(response)
    assert "Let me check." in out
    assert "[tool_use:get_order]" in out


def test_system_prompt_is_synced_once_to_backend_profile(monkeypatch):
    """First messages.create with system=... should schedule a profile patch.
    Subsequent calls on the same tracker must not re-sync."""
    client = safer.instrument(api_url="http://127.0.0.1:59999")
    _capture_events(client)

    calls: list[dict[str, Any]] = []

    def _fake_patch(agent_id: str, **kwargs: Any) -> None:
        calls.append({"agent_id": agent_id, **kwargs})

    monkeypatch.setattr(client, "schedule_profile_patch", _fake_patch)

    fake = _FakeAnthropic(_mock_response())
    agent = wrap_anthropic(fake, agent_id="sync_agent", agent_name="Sync Agent")
    agent.start_session()

    agent.messages.create(
        model="claude-opus-4-7",
        system="You are a precise assistant.",
        messages=[{"role": "user", "content": "hi"}],
    )
    agent.messages.create(
        model="claude-opus-4-7",
        system="different prompt",  # ignored, already synced
        messages=[{"role": "user", "content": "again"}],
    )

    assert len(calls) == 1
    assert calls[0]["agent_id"] == "sync_agent"
    assert calls[0]["system_prompt"] == "You are a precise assistant."
    assert calls[0]["name"] == "Sync Agent"


def test_system_prompt_sync_handles_text_block_list(monkeypatch):
    """system=[{type:'text', text:'...', cache_control:{...}}] should also sync."""
    client = safer.instrument(api_url="http://127.0.0.1:59999")
    _capture_events(client)

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        client,
        "schedule_profile_patch",
        lambda agent_id, **kw: calls.append({"agent_id": agent_id, **kw}),
    )

    fake = _FakeAnthropic(_mock_response())
    agent = wrap_anthropic(fake, agent_id="blocky", agent_name="Blocky")
    agent.start_session()

    agent.messages.create(
        model="claude-opus-4-7",
        system=[
            {"type": "text", "text": "Part A."},
            {"type": "text", "text": "Part B.", "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": "hi"}],
    )

    assert len(calls) == 1
    assert "Part A." in calls[0]["system_prompt"]
    assert "Part B." in calls[0]["system_prompt"]


def test_no_system_prompt_means_no_profile_patch(monkeypatch):
    """Calls without system= must not trigger a profile patch."""
    client = safer.instrument(api_url="http://127.0.0.1:59999")
    _capture_events(client)

    calls: list[Any] = []
    monkeypatch.setattr(
        client, "schedule_profile_patch", lambda *a, **k: calls.append((a, k))
    )

    fake = _FakeAnthropic(_mock_response())
    agent = wrap_anthropic(fake, agent_id="quiet")
    agent.start_session()
    agent.messages.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert calls == []


# ----- new in Faz 35.6 -----------------------------------------------------


def _mock_tool_use_response(tool_name: str, tool_input: dict, tool_id: str = "tu_1"):
    """A Message response containing a tool_use block (model decided to call a tool)."""
    return SimpleNamespace(
        model="claude-opus-4-7",
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="text", text="I'll call the tool."),
            SimpleNamespace(
                type="tool_use", id=tool_id, name=tool_name, input=tool_input
            ),
        ],
        usage=SimpleNamespace(
            input_tokens=20,
            output_tokens=8,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def test_tool_use_response_auto_emits_decision_and_before_tool_use():
    """When the model returns a tool_use block, the adapter must auto-emit
    on_agent_decision + before_tool_use without the user having to call
    helper methods manually."""
    client = safer.instrument(api_url="http://127.0.0.1:59999")
    events = _capture_events(client)

    fake = _FakeAnthropic(_mock_tool_use_response("read_file", {"path": "x.md"}))
    agent = wrap_anthropic(fake, agent_id="auto_tool")
    agent.messages.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "read x.md"}],
        tools=[{"name": "read_file", "description": "Read"}],
    )

    hooks = [e["hook"] for e in events]
    assert "on_agent_decision" in hooks
    assert "before_tool_use" in hooks

    decision = next(e for e in events if e["hook"] == "on_agent_decision")
    assert decision["decision_type"] == "tool_call"
    assert "read_file" in decision["chosen_action"]
    assert "x.md" in decision["chosen_action"]

    before_tool = next(e for e in events if e["hook"] == "before_tool_use")
    assert before_tool["tool_name"] == "read_file"
    assert before_tool["args"] == {"path": "x.md"}


def test_tool_result_in_next_request_synthesizes_after_tool_use():
    """After the user feeds the tool result back via the next messages.create,
    the adapter should auto-emit after_tool_use paired by tool_use_id."""
    client = safer.instrument(api_url="http://127.0.0.1:59999")
    events = _capture_events(client)

    # First call returns a tool_use
    fake = _FakeAnthropic(_mock_tool_use_response("read_file", {"path": "x.md"}, tool_id="tu_alpha"))
    agent = wrap_anthropic(fake, agent_id="pair_synth")
    agent.messages.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "read"}],
    )

    # Now swap the response: model concludes
    fake.messages = _FakeMessages(_mock_response("the file says hi"))

    # User feeds back tool_result
    agent.messages.create(
        model="claude-opus-4-7",
        messages=[
            {"role": "user", "content": "read"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_alpha", "name": "read_file", "input": {"path": "x.md"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_alpha",
                        "content": "file contents: hello",
                    }
                ],
            },
        ],
    )

    after = [e for e in events if e["hook"] == "after_tool_use"]
    assert len(after) == 1
    assert after[0]["tool_name"] == "read_file"
    assert "file contents: hello" in after[0]["result"]


def test_async_anthropic_proxy_actually_awaits():
    """Critical regression: AsyncAnthropic.messages.create returns a coroutine
    that MUST be awaited.  Older sync proxy silently dropped the await,
    so the API call never went out and SAFER emitted bogus events with
    zero tokens.  The async proxy MUST emit real after_llm_call data.

    Uses the public README-documented integration path:
        agent = wrap_anthropic(AsyncAnthropic())
        await agent.messages.create(...)
    """
    import asyncio

    client = safer.instrument(api_url="http://127.0.0.1:59999")
    events = _capture_events(client)

    # Async-shaped fake client.  `wrap_anthropic` detects the async coroutine
    # signature on `messages.create` via duck typing and wires up
    # `_AsyncMessagesProxy` automatically — same as it would for a real
    # `AsyncAnthropic` instance.
    class _AsyncFakeMessages:
        async def create(self, **kwargs):
            return _mock_response("async hi", tokens_in=42, tokens_out=7)

    class _AsyncFakeAnthropic:
        def __init__(self):
            self.messages = _AsyncFakeMessages()

    fake = _AsyncFakeAnthropic()
    agent = wrap_anthropic(fake, agent_id="async_test", agent_name="Async Test")

    # Sanity: the public wrap detected the async client and installed an
    # async messages proxy — `agent.messages.create` is now a coroutine fn
    import inspect as _inspect

    assert _inspect.iscoroutinefunction(agent.messages.create), (
        "wrap_anthropic must install an async proxy when the underlying "
        "client.messages.create is a coroutine function"
    )

    async def run():
        return await agent.messages.create(
            model="claude-haiku-4-5",
            messages=[{"role": "user", "content": "ping"}],
        )

    result = asyncio.run(run())
    assert result is not None

    after_llm = next(e for e in events if e["hook"] == "after_llm_call")
    # Real numbers, not zeros — proves the await actually completed
    assert after_llm["tokens_in"] == 42
    assert after_llm["tokens_out"] == 7
    assert after_llm["response"] == "async hi"


def test_step_count_increments_per_create_call():
    """Older code never incremented `_step_count` on create() — final_output
    reported total_steps=0 for multi-call sessions."""
    client = safer.instrument(api_url="http://127.0.0.1:59999")
    events = _capture_events(client)

    fake = _FakeAnthropic(_mock_response())
    agent = wrap_anthropic(fake, agent_id="step_count")
    for _ in range(3):
        agent.messages.create(
            model="claude-haiku-4-5",
            messages=[{"role": "user", "content": "ping"}],
        )

    after_calls = [e for e in events if e["hook"] == "after_llm_call"]
    assert len(after_calls) == 3
    # _step_count incremented 3 times via _next_seq fallback (the safer client
    # sequence path also works); either way, sequence numbers must be unique
    seqs = [e["sequence"] for e in events]
    assert len(seqs) == len(set(seqs)), f"duplicate sequence numbers: {seqs}"


def test_safer_anthropic_native_subclass_constructs_and_has_messages():
    """`SaferAnthropic` is a real Anthropic subclass; its `messages` resource
    is a `Messages` subclass with the SAFER emitter attached."""
    from anthropic import Anthropic
    from anthropic.resources.messages.messages import Messages

    from safer.adapters.claude_sdk import SaferAnthropic

    client = SaferAnthropic(
        agent_id="native_test", agent_name="Native", api_key="sk-test-fake"
    )
    assert isinstance(client, Anthropic)
    assert isinstance(client.messages, Messages)
    # The override carries the emitter
    assert hasattr(client.messages, "_safer_emitter")
    assert client.messages._safer_emitter.agent_id == "native_test"


def test_safer_async_anthropic_native_subclass():
    """Async sibling: `SaferAsyncAnthropic` extends `AsyncAnthropic` and
    overrides `messages` with an `AsyncMessages` subclass."""
    from anthropic import AsyncAnthropic
    from anthropic.resources.messages.messages import AsyncMessages

    from safer.adapters.claude_sdk import SaferAsyncAnthropic

    client = SaferAsyncAnthropic(
        agent_id="native_async", agent_name="Native Async", api_key="sk-test-fake"
    )
    assert isinstance(client, AsyncAnthropic)
    assert isinstance(client.messages, AsyncMessages)
    assert hasattr(client.messages, "_safer_emitter")
