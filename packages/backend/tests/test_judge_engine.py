"""Judge engine tests with a fake Anthropic client — no network, no API key."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from safer_backend.judge.engine import (
    JudgeMode,
    _estimate_cost,
    _extract_json,
    _extract_text,
    _parse_verdict,
    judge_event,
    set_client,
)
from safer_backend.judge.personas import SYSTEM_PROMPT
from safer_backend.models.verdicts import PersonaName, RiskLevel


class _FakeResponse:
    def __init__(self, text: str, **usage):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = SimpleNamespace(
            input_tokens=usage.get("input_tokens", 1000),
            output_tokens=usage.get("output_tokens", 400),
            cache_read_input_tokens=usage.get("cache_read", 0),
            cache_creation_input_tokens=usage.get("cache_write", 0),
        )


class _FakeAnthropic:
    def __init__(self, canned: str | list[str]) -> None:
        self._queue = [canned] if isinstance(canned, str) else list(canned)
        self.calls: list[dict] = []

        async def _create(**kwargs):
            self.calls.append(kwargs)
            text = self._queue.pop(0) if self._queue else "{}"
            return _FakeResponse(text)

        self.messages = SimpleNamespace(create=_create)


@pytest.fixture(autouse=True)
def _reset_client():
    set_client(None)
    yield
    set_client(None)


# ---------- pure helpers ----------


def test_system_prompt_contains_all_six_personas():
    required = [
        "Security Auditor",
        "Compliance Officer",
        "Trust Guardian",
        "Scope Enforcer",
        "Ethics Reviewer",
        "Policy Warden",
    ]
    for name in required:
        assert name in SYSTEM_PROMPT


def test_extract_json_strict():
    assert _extract_json('{"x": 1}') == {"x": 1}


def test_extract_json_from_prose():
    text = 'Here is the verdict:\n{"overall": {"risk": "LOW"}}\nThanks!'
    data = _extract_json(text)
    assert data["overall"]["risk"] == "LOW"


def test_extract_text_joins_text_blocks():
    resp = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="part1"),
            SimpleNamespace(type="tool_use", name="t"),
            SimpleNamespace(type="text", text="part2"),
        ]
    )
    assert _extract_text(resp) == "part1\npart2"


def test_cost_estimation_cache_savings():
    # With 900 of 1000 input tokens cached, savings should be dramatic.
    cold = _estimate_cost("claude-opus-4-7", 1000, 0, 0, 0)
    hot = _estimate_cost("claude-opus-4-7", 1000, 0, 900, 0)
    assert hot < cold * 0.2  # at least 80% reduction


# ---------- parse_verdict ----------


def _good_verdict_json() -> dict:
    return {
        "overall": {"risk": "HIGH", "confidence": 0.9, "block": True},
        "active_personas": ["security_auditor", "policy_warden"],
        "personas": {
            "security_auditor": {
                "persona": "security_auditor",
                "score": 30,
                "confidence": 0.95,
                "flags": ["prompt_injection_direct", "tool_abuse"],
                "evidence": ["ignore previous instructions"],
                "reasoning": "clear prompt injection attempt",
                "recommended_mitigation": "sanitize user input",
            },
            "policy_warden": {
                "persona": "policy_warden",
                "score": 40,
                "confidence": 0.8,
                "flags": ["policy_violation"],
                "evidence": ["violates no-email-export policy"],
                "reasoning": "tool call violates active policy",
                "recommended_mitigation": None,
            },
        },
    }


def test_parse_verdict_happy_path():
    verdict = _parse_verdict(
        data=_good_verdict_json(),
        event_id="evt_x",
        session_id="sess_x",
        agent_id="agent_x",
        mode=JudgeMode.RUNTIME,
        latency_ms=1200,
        tokens_in=1000,
        tokens_out=400,
        cache_read=0,
        cost=0.05,
    )
    assert verdict.overall.risk == RiskLevel.HIGH
    assert verdict.overall.block is True
    assert PersonaName.SECURITY_AUDITOR in verdict.personas
    assert len(verdict.personas[PersonaName.SECURITY_AUDITOR].flags) == 2


def test_parse_verdict_drops_unknown_flags():
    data = _good_verdict_json()
    data["personas"]["security_auditor"]["flags"].append("definitely_not_a_real_flag")
    verdict = _parse_verdict(
        data=data,
        event_id="evt_x",
        session_id="sess_x",
        agent_id="agent_x",
        mode=JudgeMode.RUNTIME,
        latency_ms=0,
        tokens_in=0,
        tokens_out=0,
        cache_read=0,
        cost=0.0,
    )
    flags = verdict.personas[PersonaName.SECURITY_AUDITOR].flags
    assert "definitely_not_a_real_flag" not in flags
    assert "prompt_injection_direct" in flags


# ---------- end-to-end judge_event with fake client ----------


@pytest.mark.asyncio
async def test_judge_event_happy_path_fake_client(monkeypatch):
    # Pretend API key is set so engine activates client path.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    fake = _FakeAnthropic(json.dumps(_good_verdict_json()))
    set_client(fake)

    verdict = await judge_event(
        event={
            "event_id": "evt_1",
            "session_id": "sess_1",
            "agent_id": "agent_1",
            "hook": "before_tool_use",
            "sequence": 0,
            "tool_name": "send_email",
            "args": {"to": "attacker@example.com"},
        },
        active_personas=["security_auditor", "policy_warden"],
        mode=JudgeMode.RUNTIME,
    )

    assert verdict.overall.block is True
    assert len(fake.calls) == 1
    # Prompt cache must be requested.
    system = fake.calls[0]["system"]
    assert isinstance(system, list)
    assert system[0].get("cache_control") == {"type": "ephemeral"}
    # `temperature` was deprecated by Anthropic; we must not send it.
    assert "temperature" not in fake.calls[0]


@pytest.mark.asyncio
async def test_judge_event_repairs_malformed_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    bad = "Here is the verdict: oops not json"
    good = json.dumps(_good_verdict_json())
    fake = _FakeAnthropic([bad, good])
    set_client(fake)

    verdict = await judge_event(
        event={
            "event_id": "evt_2",
            "session_id": "sess_2",
            "agent_id": "agent_2",
            "hook": "on_final_output",
            "sequence": 0,
            "final_response": "I finished.",
        },
        active_personas=["security_auditor"],
        mode=JudgeMode.RUNTIME,
    )
    assert verdict.overall.risk == RiskLevel.HIGH
    assert len(fake.calls) == 2  # original + one repair pass


@pytest.mark.asyncio
async def test_judge_event_raises_without_client():
    # No API key, no injected client → RuntimeError.
    import os

    os.environ.pop("ANTHROPIC_API_KEY", None)
    set_client(None)

    with pytest.raises(RuntimeError):
        await judge_event(
            event={"hook": "on_final_output", "session_id": "s", "agent_id": "a", "sequence": 0, "final_response": "x"},
            active_personas=["security_auditor"],
        )
