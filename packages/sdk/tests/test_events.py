"""Round-trip tests for 9-hook event payloads."""

from __future__ import annotations

import pytest

from safer.events import (
    HOOK_TO_PAYLOAD,
    AfterLLMCallPayload,
    AfterToolUsePayload,
    BeforeLLMCallPayload,
    BeforeToolUsePayload,
    Hook,
    OnAgentDecisionPayload,
    OnErrorPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnSessionStartPayload,
    parse_event,
)


def _make(cls, **kwargs):
    base = dict(
        session_id="sess_test",
        agent_id="agent_test",
        sequence=0,
    )
    base.update(kwargs)
    return cls(**base)


@pytest.mark.parametrize(
    "cls,extra",
    [
        (OnSessionStartPayload, {"agent_name": "demo"}),
        (BeforeLLMCallPayload, {"model": "claude-opus-4-7", "prompt": "hello"}),
        (
            AfterLLMCallPayload,
            {"model": "claude-opus-4-7", "response": "hi", "tokens_in": 5, "tokens_out": 3},
        ),
        (BeforeToolUsePayload, {"tool_name": "get_order", "args": {"id": 42}}),
        (AfterToolUsePayload, {"tool_name": "get_order", "result": {"status": "OK"}}),
        (OnAgentDecisionPayload, {"decision_type": "select_tool", "chosen_action": "get_order"}),
        (OnFinalOutputPayload, {"final_response": "done", "total_steps": 3}),
        (OnSessionEndPayload, {"total_duration_ms": 1200, "success": True}),
        (OnErrorPayload, {"error_type": "TimeoutError", "message": "boom"}),
    ],
)
def test_event_round_trip(cls, extra):
    event = _make(cls, **extra)
    raw = event.model_dump(mode="json")
    parsed = parse_event(raw)
    assert type(parsed) is cls
    assert parsed.session_id == "sess_test"


def test_parse_event_discriminates_by_hook():
    data = {
        "session_id": "sess_x",
        "agent_id": "agent_x",
        "sequence": 0,
        "hook": "before_tool_use",
        "tool_name": "do_thing",
        "args": {"k": "v"},
    }
    event = parse_event(data)
    assert isinstance(event, BeforeToolUsePayload)
    assert event.tool_name == "do_thing"


def test_hook_enum_matches_payload_map():
    assert set(HOOK_TO_PAYLOAD.keys()) == set(Hook)
    # 1 onboarding (on_agent_register) + 9 runtime hooks.
    assert len(HOOK_TO_PAYLOAD) == 10
