"""Persona routing rules — deterministic, table-driven tests."""

from __future__ import annotations

from safer.events import Hook, RiskHint

from safer_backend.models.verdicts import PersonaName
from safer_backend.router.persona_router import (
    INSPECTOR_PERSONAS,
    is_judge_eligible,
    select_personas_inspector,
    select_personas_runtime,
)


def test_judge_eligible_hooks():
    # Exactly 3 hooks should trigger Judge.
    eligible = [h for h in Hook if is_judge_eligible(h)]
    assert set(eligible) == {
        Hook.BEFORE_TOOL_USE,
        Hook.ON_AGENT_DECISION,
        Hook.ON_FINAL_OUTPUT,
    }


def test_before_tool_use_personas():
    personas = select_personas_runtime(Hook.BEFORE_TOOL_USE, RiskHint.LOW)
    assert set(personas) == {
        PersonaName.SECURITY_AUDITOR,
        PersonaName.COMPLIANCE_OFFICER,
        PersonaName.SCOPE_ENFORCER,
        PersonaName.POLICY_WARDEN,
    }


def test_agent_decision_low_risk_minimal_set():
    personas = select_personas_runtime(Hook.ON_AGENT_DECISION, RiskHint.LOW)
    assert set(personas) == {PersonaName.SCOPE_ENFORCER, PersonaName.POLICY_WARDEN}


def test_agent_decision_elevated_risk_adds_security_and_trust():
    personas = select_personas_runtime(Hook.ON_AGENT_DECISION, RiskHint.MEDIUM)
    assert PersonaName.SECURITY_AUDITOR in personas
    assert PersonaName.TRUST_GUARDIAN in personas
    assert PersonaName.SCOPE_ENFORCER in personas
    assert PersonaName.POLICY_WARDEN in personas


def test_final_output_all_six_personas():
    personas = select_personas_runtime(Hook.ON_FINAL_OUTPUT, RiskHint.LOW)
    assert set(personas) == set(PersonaName)


def test_non_eligible_hooks_return_empty():
    for hook in [
        Hook.ON_SESSION_START,
        Hook.BEFORE_LLM_CALL,
        Hook.AFTER_LLM_CALL,
        Hook.AFTER_TOOL_USE,
        Hook.ON_SESSION_END,
        Hook.ON_ERROR,
    ]:
        assert select_personas_runtime(hook, RiskHint.LOW) == []
        assert not is_judge_eligible(hook)


def test_inspector_personas_fixed_three():
    personas = select_personas_inspector()
    assert personas == INSPECTOR_PERSONAS
    assert set(personas) == {
        PersonaName.SECURITY_AUDITOR,
        PersonaName.COMPLIANCE_OFFICER,
        PersonaName.POLICY_WARDEN,
    }


def test_accepts_string_inputs():
    personas = select_personas_runtime("on_final_output", "LOW")
    assert len(personas) == 6
    assert is_judge_eligible("before_tool_use")
    assert not is_judge_eligible("after_tool_use")
