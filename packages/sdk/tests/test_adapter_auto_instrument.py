"""Constructor-level idempotent auto-instrument for SAFER adapters.

All five adapter classes call `ensure_runtime(...)` as their first
action so the two-line integration pattern works even when the user
never explicitly calls `instrument()`. These tests verify the
contract for the three adapters that already ship (Claude SDK,
OpenAI, LangChain); Google ADK and Strands adapters are covered by
their own test modules under 33.5 / 33.6.
"""

from __future__ import annotations

import os

import pytest

from safer import client as client_mod
from safer.instrument import _reset_registered_agents_for_tests


@pytest.fixture(autouse=True)
def _reset_client_between_tests(monkeypatch):
    """Every test starts from a pristine SAFER runtime — no live client,
    no remembered registered agents."""
    client_mod._client = None
    _reset_registered_agents_for_tests()
    # Point the runtime at a local no-op URL so the real SaferClient can
    # still start without trying to contact a real backend.
    monkeypatch.setenv("SAFER_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("SAFER_TRANSPORT_MODE", "http")
    yield
    client_mod._client = None
    _reset_registered_agents_for_tests()


def test_claude_adapter_constructor_starts_runtime():
    from anthropic import Anthropic

    from safer.adapters.claude_sdk import wrap_anthropic

    assert client_mod._client is None, "precondition: no runtime"
    wrap_anthropic(
        Anthropic(api_key="sk-test"),
        agent_id="claude_auto",
        agent_name="Claude Auto",
    )
    assert client_mod._client is not None, "adapter must have started the runtime"
    assert client_mod._client.config.agent_id == "claude_auto"


def test_openai_adapter_constructor_starts_runtime():
    from safer.adapters.openai_agents import wrap_openai

    assert client_mod._client is None
    # We don't need a real OpenAI client for construction — _OpenAIAdapter
    # only stores the inner object.
    wrap_openai(
        object(),
        agent_id="openai_auto",
        agent_name="OpenAI Auto",
    )
    assert client_mod._client is not None
    assert client_mod._client.config.agent_id == "openai_auto"


def test_langchain_handler_constructor_starts_runtime():
    pytest.importorskip("langchain_core")
    from safer.adapters.langchain import SaferCallbackHandler

    assert client_mod._client is None
    SaferCallbackHandler(agent_id="lc_auto", agent_name="LC Auto")
    assert client_mod._client is not None
    assert client_mod._client.config.agent_id == "lc_auto"


def test_constructor_is_noop_when_runtime_already_exists(monkeypatch):
    """If the user calls instrument() themselves first, the adapter
    constructor must not replace the existing client or rerun detection.
    """
    from safer import instrument

    # Pre-configure the runtime with a custom api_url.
    monkeypatch.setenv("SAFER_API_URL", "http://custom.example.com:9000")
    instrument(agent_id="manual_user", agent_name="Manual")
    pre_client = client_mod._client
    assert pre_client is not None
    pre_url = pre_client.config.api_url

    # Now the adapter is instantiated with a *different* agent_id — the
    # client instance must stay the same and api_url unchanged.
    from anthropic import Anthropic

    from safer.adapters.claude_sdk import wrap_anthropic

    wrap_anthropic(
        Anthropic(api_key="sk-test"),
        agent_id="different_agent",
        agent_name="Different",
    )
    assert client_mod._client is pre_client, "adapter must not replace the client"
    assert client_mod._client.config.api_url == pre_url
    # Original agent_id on config is preserved.
    assert client_mod._client.config.agent_id == "manual_user"
