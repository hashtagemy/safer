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
