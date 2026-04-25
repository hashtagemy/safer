"""Tests for the OpenAI partial adapter + the three beta stubs."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest


# ---------- OpenAI partial ----------


@pytest.fixture()
def recording_client(monkeypatch):
    calls: list[dict] = []

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
            """Record events from adapters that go through `client.emit()`
            rather than `client.track_event()` (e.g. claude_sdk, openai_client)."""
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
            # Simple counter so adapter doesn't fall back to internal counter
            n = getattr(self, "_seq_counter", 0)
            self._seq_counter = n + 1
            return n

    from safer import client as client_mod

    monkeypatch.setattr(client_mod, "_client", _Dummy(), raising=False)
    return calls


def _make_fake_openai():
    """Minimal object shaped enough like an OpenAI client for wrap_openai."""

    def create(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hello there"),
                    text=None,
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
        )

    completions = SimpleNamespace(create=create)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(chat=chat)


def test_openai_wrap_emits_before_and_after(recording_client):
    from safer.adapters.openai_agents import wrap_openai

    client = wrap_openai(_make_fake_openai(), agent_id="agent_openai")
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert resp.choices[0].message.content == "hello there"

    hooks = [c["hook"] for c in recording_client]
    assert hooks[0] == "on_session_start"
    assert "before_llm_call" in hooks
    assert "after_llm_call" in hooks


def test_openai_wrap_records_on_error(recording_client):
    from safer.adapters.openai_agents import wrap_openai

    def boom(**_kwargs):
        raise RuntimeError("api exploded")

    inner = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    client = wrap_openai(inner, agent_id="agent_err")
    with pytest.raises(RuntimeError):
        client.chat.completions.create(model="gpt-4o", messages=[])

    hooks = [c["hook"] for c in recording_client]
    assert "on_error" in hooks


# ---------- beta stubs ----------

# Note: Google ADK used to be a no-op stub; it is now a real adapter.
# Its coverage lives in test_adapters_google_adk.py (full Plugin flow
# after Faz 33.5).


def test_bedrock_stub_is_noop_with_warning(caplog):
    from safer.adapters.bedrock import wrap_bedrock

    sentinel = object()
    with caplog.at_level(logging.WARNING, logger="safer.adapters.bedrock"):
        out = wrap_bedrock(sentinel, agent_id="x")
    assert out is sentinel
    assert any("bedrock" in rec.message for rec in caplog.records)


def test_crewai_stub_is_noop_with_warning(caplog):
    from safer.adapters.crewai import wrap_crew

    sentinel = object()
    with caplog.at_level(logging.WARNING, logger="safer.adapters.crewai"):
        out = wrap_crew(sentinel, agent_id="x")
    assert out is sentinel
    assert any("crewai" in rec.message for rec in caplog.records)
