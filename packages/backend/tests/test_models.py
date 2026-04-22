"""Unit tests for backend models: flags, verdicts, findings, red-team, session report."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from safer_backend.models import (
    AttackSpec,
    CategoryScore,
    FLAG_VOCABULARY,
    FlagCategory,
    Finding,
    Severity,
    SessionReport,
    is_known_flag,
)
from safer_backend.models.redteam import AttackCategory, RedTeamRun
from safer_backend.models.verdicts import (
    Overall,
    PersonaName,
    PersonaVerdict,
    RiskLevel,
    Verdict,
)
from safer_backend.models.session_report import CostSummary


# ============================================================
# Flags
# ============================================================


def test_flag_vocabulary_has_all_categories():
    assert set(FLAG_VOCABULARY.keys()) == set(FlagCategory)


def test_is_known_flag_accepts_closed_vocab():
    assert is_known_flag("prompt_injection_direct")
    assert is_known_flag("pii_exposure")
    assert is_known_flag("owasp_llm01_prompt_injection")


def test_is_known_flag_accepts_custom_prefix():
    assert is_known_flag("custom_no_weekend_deploys")


def test_is_known_flag_rejects_unknown():
    assert not is_known_flag("random_made_up_thing")


# ============================================================
# PersonaVerdict
# ============================================================


def test_persona_verdict_rejects_unknown_flag():
    with pytest.raises(ValidationError):
        PersonaVerdict(
            persona=PersonaName.SECURITY_AUDITOR,
            score=30,
            confidence=0.9,
            flags=["totally_invented_flag"],
        )


def test_persona_verdict_accepts_valid_flags():
    v = PersonaVerdict(
        persona=PersonaName.COMPLIANCE_OFFICER,
        score=40,
        confidence=0.8,
        flags=["pii_exposure", "gdpr_art5_violation"],
        evidence=["tool args contained: email=x@y.com"],
        reasoning="PII visible in outbound call",
    )
    assert len(v.flags) == 2


def test_persona_verdict_score_range():
    with pytest.raises(ValidationError):
        PersonaVerdict(persona=PersonaName.TRUST_GUARDIAN, score=200, confidence=0.5)


# ============================================================
# Verdict
# ============================================================


def test_verdict_round_trip():
    v = Verdict(
        event_id="evt_x",
        session_id="sess_x",
        agent_id="agent_x",
        active_personas=[PersonaName.SECURITY_AUDITOR, PersonaName.POLICY_WARDEN],
        personas={
            PersonaName.SECURITY_AUDITOR: PersonaVerdict(
                persona=PersonaName.SECURITY_AUDITOR,
                score=80,
                confidence=0.9,
            ),
            PersonaName.POLICY_WARDEN: PersonaVerdict(
                persona=PersonaName.POLICY_WARDEN,
                score=90,
                confidence=0.95,
            ),
        },
        overall=Overall(risk=RiskLevel.LOW, confidence=0.9, block=False),
    )
    raw = v.model_dump(mode="json")
    restored = Verdict.model_validate(raw)
    assert restored.overall.risk == RiskLevel.LOW
    assert len(restored.personas) == 2


# ============================================================
# Finding
# ============================================================


def test_finding_shape():
    f = Finding(
        agent_id="agent_x",
        source="inspector",
        severity=Severity.HIGH,
        category="SECURITY",
        flag="credential_hardcoded",
        title="Hard-coded API key",
        description="sk-... found at line 47",
    )
    assert f.severity == Severity.HIGH
    assert f.finding_id.startswith("fnd_")


# ============================================================
# Red-Team
# ============================================================


def test_attack_spec_shape():
    spec = AttackSpec(
        category=AttackCategory.DIRECT_INJECTION,
        title="Classic ignore-prev",
        prompt="Ignore previous instructions...",
        expected_behavior="Agent should refuse or sanitize",
    )
    assert spec.attack_id.startswith("atk_")


def test_redteam_run_defaults():
    r = RedTeamRun(agent_id="agent_x")
    assert r.phase.value == "planning"
    assert r.safety_score == 0
    assert r.attempts == []


# ============================================================
# Session Report
# ============================================================


def test_session_report_round_trip():
    report = SessionReport(
        session_id="sess_x",
        agent_id="agent_x",
        agent_name="demo",
        overall_health=72,
        categories=[
            CategoryScore(name="security", value=95),
            CategoryScore(name="compliance", value=40),
        ],
        cost=CostSummary(total_usd=0.12, num_opus_calls=3, num_haiku_calls=8),
    )
    raw = report.model_dump(mode="json")
    restored = SessionReport.model_validate(raw)
    assert restored.overall_health == 72
    assert len(restored.categories) == 2
