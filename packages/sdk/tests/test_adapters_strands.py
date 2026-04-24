"""Strands Agents adapter tests.

The `safer.adapters.strands` module ships `SaferHookProvider` — a
`HookProvider` implementation that registers eight callbacks with
the Strands `HookRegistry`. These tests drive it with duck-typed
Strands event objects (message dicts with `role`/`content`, tool
use/result dicts, `AgentResult` stand-in) so the tests stay focused
on the adapter's own mapping logic.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("strands.hooks")

from safer import client as client_mod
from safer.instrument import _reset_registered_agents_for_tests


@pytest.fixture(autouse=True)
def _reset_runtime_and_install_dummy(monkeypatch):
    client_mod._client = None
    _reset_registered_agents_for_tests()
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

        def schedule_profile_patch(self, agent_id, **kw):
            calls.append({"hook": "__profile_patch__", "agent_id": agent_id, **kw})

    monkeypatch.setattr(client_mod, "_client", _Dummy(), raising=False)
    yield calls
    client_mod._client = None
    _reset_registered_agents_for_tests()


# ----- duck-typed Strands objects ------------------------------------


def _make_agent(
    *,
    system_prompt: str | None = "You are helpful.",
    model_id: str = "claude-opus-4-7",
    tools: list[str] | None = None,
    messages: list[dict] | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> SimpleNamespace:
    model = SimpleNamespace(model_id=model_id, config={"model_id": model_id})
    tool_registry = SimpleNamespace(tool_names=list(tools or []))
    metrics = SimpleNamespace(
        accumulated_usage={
            "inputTokens": tokens_in,
            "outputTokens": tokens_out,
            "cacheReadInputTokens": 0,
        }
    )
    return SimpleNamespace(
        model=model,
        tool_registry=tool_registry,
        messages=list(messages or []),
        system_prompt=system_prompt,
        event_loop_metrics=metrics,
    )


def _user_message(text: str) -> dict:
    return {"role": "user", "content": [{"text": text}]}


def _assistant_message(text: str) -> dict:
    return {"role": "assistant", "content": [{"text": text}]}


def _assistant_tool_use_message(tool_name: str, tool_input: dict) -> dict:
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": "tu_1",
                    "name": tool_name,
                    "input": tool_input,
                }
            }
        ],
    }


# ----- tests ---------------------------------------------------------


def test_module_imports_without_running_the_provider():
    mod = importlib.import_module("safer.adapters.strands")
    assert hasattr(mod, "SaferHookProvider")


def test_provider_registers_eight_callbacks():
    from strands.hooks import (
        AfterInvocationEvent,
        AfterModelCallEvent,
        AfterToolCallEvent,
        AgentInitializedEvent,
        BeforeInvocationEvent,
        BeforeModelCallEvent,
        BeforeToolCallEvent,
        MessageAddedEvent,
    )

    from safer.adapters.strands import SaferHookProvider

    provider = SaferHookProvider(agent_id="x", agent_name="X")
    registered: list[tuple[type, Any]] = []

    class _FakeRegistry:
        def add_callback(self, event_type: type, callback: Any) -> None:
            registered.append((event_type, callback))

    provider.register_hooks(_FakeRegistry())
    types_registered = [t for t, _ in registered]
    assert set(types_registered) == {
        AgentInitializedEvent,
        BeforeInvocationEvent,
        AfterInvocationEvent,
        MessageAddedEvent,
        BeforeModelCallEvent,
        AfterModelCallEvent,
        BeforeToolCallEvent,
        AfterToolCallEvent,
    }


def test_full_flow_emits_nine_safer_hooks(_reset_runtime_and_install_dummy):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.strands import SaferHookProvider

    provider = SaferHookProvider(agent_id="s_full", agent_name="Strands Full")

    agent = _make_agent(
        tools=["list_processes", "disk_usage"],
        messages=[_user_message("check system")],
        tokens_in=80,
        tokens_out=15,
    )

    before_inv = SimpleNamespace(agent=agent, invocation_state={}, messages=agent.messages)
    provider._on_before_invocation(before_inv)

    provider._on_before_model_call(SimpleNamespace(agent=agent, invocation_state={}))
    after_model = SimpleNamespace(
        agent=agent,
        invocation_state={},
        stop_response=SimpleNamespace(
            message=_assistant_tool_use_message("disk_usage", {}),
            stop_reason="tool_use",
        ),
        exception=None,
        retry=False,
    )
    provider._on_after_model_call(after_model)

    agent.messages.append(_assistant_tool_use_message("disk_usage", {}))
    provider._on_message_added(SimpleNamespace(agent=agent, message=agent.messages[-1]))

    tool_use = {"toolUseId": "tu_1", "name": "disk_usage", "input": {}}
    provider._on_before_tool_call(
        SimpleNamespace(
            agent=agent,
            selected_tool=None,
            tool_use=tool_use,
            invocation_state={},
            cancel_tool=False,
        )
    )
    tool_result = {
        "toolUseId": "tu_1",
        "status": "success",
        "content": [{"text": "Filesystem 1K-blocks Used..."}],
    }
    provider._on_after_tool_call(
        SimpleNamespace(
            agent=agent,
            selected_tool=None,
            tool_use=tool_use,
            invocation_state={},
            result=tool_result,
            exception=None,
            cancel_message=None,
            retry=False,
        )
    )

    final_result = SimpleNamespace(
        stop_reason="end_turn",
        message=_assistant_message("All good, 45% used."),
        metrics=agent.event_loop_metrics,
        state={},
        interrupts=None,
        structured_output=None,
    )
    provider._on_after_invocation(
        SimpleNamespace(
            agent=agent,
            invocation_state={},
            result=final_result,
            resume=None,
        )
    )

    hooks = [c["hook"] for c in calls if c["hook"] != "__profile_patch__"]
    for required in (
        "on_session_start",
        "before_llm_call",
        "after_llm_call",
        "on_agent_decision",
        "before_tool_use",
        "after_tool_use",
        "on_final_output",
        "on_session_end",
    ):
        assert required in hooks, f"missing hook: {required}"

    after_llm = next(c for c in calls if c["hook"] == "after_llm_call")
    assert after_llm["payload"]["tokens_in"] == 80
    assert after_llm["payload"]["tokens_out"] == 15
    assert after_llm["payload"]["model"] == "claude-opus-4-7"
    assert after_llm["payload"]["cost_usd"] > 0

    final = next(c for c in calls if c["hook"] == "on_final_output")
    assert "45% used" in final["payload"]["final_response"]


def test_after_model_exception_emits_on_error(_reset_runtime_and_install_dummy):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.strands import SaferHookProvider

    provider = SaferHookProvider(agent_id="s_err")
    agent = _make_agent(messages=[_user_message("hi")])
    provider._on_after_model_call(
        SimpleNamespace(
            agent=agent,
            invocation_state={},
            stop_response=None,
            exception=RuntimeError("anthropic 529"),
            retry=False,
        )
    )
    errs = [c for c in calls if c["hook"] == "on_error"]
    assert len(errs) == 1
    assert "model_error" in errs[0]["payload"]["error_type"]


def test_after_tool_error_status_emits_on_error(_reset_runtime_and_install_dummy):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.strands import SaferHookProvider

    provider = SaferHookProvider(agent_id="s_terr")
    agent = _make_agent()
    tool_use = {"toolUseId": "tu_err", "name": "restart_service", "input": {}}
    tool_result = {
        "toolUseId": "tu_err",
        "status": "error",
        "content": [{"text": "permission denied"}],
    }
    provider._on_after_tool_call(
        SimpleNamespace(
            agent=agent,
            selected_tool=None,
            tool_use=tool_use,
            invocation_state={},
            result=tool_result,
            exception=None,
            cancel_message="denied",
            retry=False,
        )
    )
    errs = [c for c in calls if c["hook"] == "on_error"]
    assert len(errs) == 1
    assert "tool_error" in errs[0]["payload"]["error_type"]
    assert "permission denied" in errs[0]["payload"]["message"]


def test_provider_constructor_auto_instruments(monkeypatch):
    """Pristine runtime + construct provider → client is now running."""
    from safer import client as client_mod  # re-bind inside the test
    from safer.instrument import _reset_registered_agents_for_tests

    client_mod._client = None
    _reset_registered_agents_for_tests()
    monkeypatch.setenv("SAFER_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("SAFER_TRANSPORT_MODE", "http")
    assert client_mod._client is None

    from safer.adapters.strands import SaferHookProvider

    SaferHookProvider(agent_id="auto_s", agent_name="Auto")
    assert client_mod._client is not None
    assert client_mod._client.config.agent_id == "auto_s"


def test_message_added_non_assistant_role_is_ignored(_reset_runtime_and_install_dummy):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.strands import SaferHookProvider

    provider = SaferHookProvider(agent_id="s_msg")
    agent = _make_agent()
    provider._on_message_added(
        SimpleNamespace(agent=agent, message=_user_message("hi"))
    )
    # User messages must not synthesize on_agent_decision.
    assert not any(c["hook"] == "on_agent_decision" for c in calls)


def test_session_start_fires_exactly_once(_reset_runtime_and_install_dummy):
    calls = _reset_runtime_and_install_dummy
    from safer.adapters.strands import SaferHookProvider

    provider = SaferHookProvider(agent_id="s_once")
    agent = _make_agent()
    provider._on_before_invocation(
        SimpleNamespace(agent=agent, invocation_state={}, messages=[])
    )
    provider._on_before_model_call(SimpleNamespace(agent=agent, invocation_state={}))
    starts = [c for c in calls if c["hook"] == "on_session_start"]
    assert len(starts) == 1
