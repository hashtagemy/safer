"""Inspector orchestrator falls back to the sub-agent path when the
Managed-Agents implementation raises `InspectorManagedError`.
"""

from __future__ import annotations

import pytest

from safer_backend.inspector import orchestrator as orch_mod
from safer_backend.inspector.managed import InspectorManagedError
from safer_backend.models.inspector import ASTSummary
from safer_backend.models.verdicts import (
    Overall,
    PersonaName,
    PersonaVerdict,
    RiskLevel,
    Verdict,
)


def _fake_verdict(agent_id: str) -> Verdict:
    persona = PersonaVerdict(
        persona=PersonaName.SECURITY_AUDITOR,
        score=90,
        confidence=0.8,
        flags=[],
        evidence=[],
        reasoning="ok",
    )
    return Verdict(
        event_id=f"ins_{agent_id}",
        session_id="",
        agent_id=agent_id,
        mode="INSPECTOR",
        active_personas=[PersonaName.SECURITY_AUDITOR],
        personas={PersonaName.SECURITY_AUDITOR: persona},
        overall=Overall(risk=RiskLevel.LOW, confidence=0.8, block=False),
    )


@pytest.mark.asyncio
async def test_orchestrator_uses_managed_when_available(monkeypatch):
    """Happy path: managed returns a verdict; sub-agent is never called."""
    calls: list[str] = []

    async def fake_managed(**kwargs):
        calls.append("managed")
        return _fake_verdict(kwargs["agent_id"])

    async def fake_subagent(**kwargs):
        calls.append("subagent")
        return _fake_verdict(kwargs["agent_id"])

    import safer_backend.inspector.managed as managed_mod

    monkeypatch.setattr(managed_mod, "review_managed", fake_managed)
    monkeypatch.setattr(orch_mod, "persona_review", fake_subagent)

    report = await orch_mod.inspect(
        agent_id="agent_x",
        source="def hello():\n    return 1\n",
    )

    assert calls == ["managed"]
    assert report.persona_review_mode == "managed"
    assert not report.persona_review_skipped


@pytest.mark.asyncio
async def test_orchestrator_falls_back_on_managed_error(monkeypatch):
    """If managed raises InspectorManagedError the sub-agent picks up."""
    calls: list[str] = []

    async def fake_managed(**kwargs):
        calls.append("managed")
        raise InspectorManagedError("simulated failure")

    async def fake_subagent(**kwargs):
        calls.append("subagent")
        return _fake_verdict(kwargs["agent_id"])

    import safer_backend.inspector.managed as managed_mod

    monkeypatch.setattr(managed_mod, "review_managed", fake_managed)
    monkeypatch.setattr(orch_mod, "persona_review", fake_subagent)

    report = await orch_mod.inspect(
        agent_id="agent_y",
        source="x = 1\n",
    )

    assert calls == ["managed", "subagent"]
    assert report.persona_review_mode == "managed_fallback_subagent"
    assert not report.persona_review_skipped


@pytest.mark.asyncio
async def test_project_scan_falls_back_on_managed_error(monkeypatch):
    """inspect_project uses the same fallback semantics."""
    calls: list[str] = []

    async def fake_managed(**kwargs):
        calls.append("managed")
        raise InspectorManagedError("simulated failure")

    async def fake_subagent_project(**kwargs):
        calls.append("subagent_project")
        return _fake_verdict(kwargs["agent_id"])

    import safer_backend.inspector.managed as managed_mod

    monkeypatch.setattr(managed_mod, "review_managed", fake_managed)
    monkeypatch.setattr(orch_mod, "persona_review_project", fake_subagent_project)

    report = await orch_mod.inspect_project(
        agent_id="agent_z",
        files=[("a.py", "def a():\n    return 1\n")],
    )

    assert calls == ["managed", "subagent_project"]
    assert report.persona_review_mode == "managed_fallback_subagent"
