"""Haiku per-step scoring tests with a fake Anthropic client."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from safer_backend.router.haiku_prestep import (
    _DEFAULT,
    score_step,
    set_client,
)


class _FakeResp:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = SimpleNamespace(
            input_tokens=80, output_tokens=30,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )


class _FakeHaiku:
    def __init__(self, body):
        self._body = body
        self.calls = []

        async def _create(**kwargs):
            self.calls.append(kwargs)
            return _FakeResp(self._body if isinstance(self._body, str) else json.dumps(self._body))

        self.messages = SimpleNamespace(create=_create)


@pytest.fixture(autouse=True)
def _reset():
    set_client(None)
    yield
    set_client(None)


@pytest.mark.asyncio
async def test_non_decision_hook_returns_default():
    score = await score_step({"hook": "after_tool_use", "session_id": "s", "agent_id": "a"})
    assert score is _DEFAULT


@pytest.mark.asyncio
async def test_decision_hook_scores_via_fake_haiku(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake = _FakeHaiku({"relevance_score": 42, "should_escalate": True, "reason": "drift"})
    set_client(fake)
    score = await score_step(
        {
            "hook": "before_tool_use",
            "session_id": "s",
            "agent_id": "a",
            "event_id": "evt_x",
            "tool_name": "search",
            "args": {"q": "unrelated"},
        },
        user_goal="check order status",
    )
    assert score.relevance_score == 42
    assert score.should_escalate is True
    assert score.reason == "drift"
    # Prompt cache must be requested.
    system = fake.calls[0]["system"]
    assert system[0].get("cache_control") == {"type": "ephemeral"}
    # `temperature` was deprecated by Anthropic; we must not send it.
    assert "temperature" not in fake.calls[0]


@pytest.mark.asyncio
async def test_missing_client_returns_default(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    set_client(None)
    score = await score_step(
        {"hook": "before_llm_call", "session_id": "s", "agent_id": "a"}
    )
    assert score is _DEFAULT


@pytest.mark.asyncio
async def test_malformed_json_returns_safe_defaults(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake = _FakeHaiku("not json at all")
    set_client(fake)
    score = await score_step(
        {"hook": "on_agent_decision", "session_id": "s", "agent_id": "a"}
    )
    # Defaults when parsing fails: 100 / False.
    assert score.relevance_score == 100
    assert score.should_escalate is False
