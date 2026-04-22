"""SaferClient tests — instrument() idempotency, track_event, Custom SDK path."""

from __future__ import annotations

import pytest

import safer
from safer import Hook, SaferBlocked
from safer.client import clear_client


@pytest.fixture(autouse=True)
def _reset_client():
    clear_client()
    yield
    clear_client()


def test_instrument_is_idempotent():
    c1 = safer.instrument(api_url="http://127.0.0.1:59999")
    c2 = safer.instrument(api_url="http://127.0.0.1:59999")
    assert c1 is c2


def test_track_event_without_framework():
    safer.instrument(api_url="http://127.0.0.1:59999")
    # Should not raise: vanilla python agent use case.
    safer.track_event(
        Hook.BEFORE_LLM_CALL,
        {"model": "claude-opus-4-7", "prompt": "hi"},
        session_id="sess_test",
        agent_id="agent_test",
    )


def test_safer_blocked_carries_verdict():
    verdict = {"overall": {"risk": "CRITICAL", "block": True}}
    exc = SaferBlocked(verdict=verdict, event_id="evt_x", message="PII leak")
    assert exc.verdict == verdict
    assert exc.event_id == "evt_x"
    assert exc.message == "PII leak"
    assert str(exc) == "PII leak"
