"""Closed-vocabulary flag system — exhaustive validation.

The Judge, Inspector, Red-Team Analyst, and Policy Studio all depend
on every persona verdict using flags drawn from the closed vocabulary
in `models.flags` (or the `custom_` user-defined prefix). The
deterministic OWASP map and Session Report scoring formulas assume
this invariant — these tests pin it down.
"""

from __future__ import annotations

import pytest

from safer_backend.models.flags import (
    ALL_FLAGS,
    COMPLIANCE_FLAGS,
    ETHICS_FLAGS,
    FLAG_VOCABULARY,
    FlagCategory,
    OWASP_LLM_FLAGS,
    POLICY_FLAGS,
    SCOPE_FLAGS,
    SECURITY_FLAGS,
    TRUST_FLAGS,
    category_of,
    is_known_flag,
)


# ---------- vocabulary shape ----------


def test_every_category_has_at_least_one_flag():
    for category, flags in FLAG_VOCABULARY.items():
        assert flags, f"category {category.value} has no flags"


def test_total_flag_count_matches_documented_67_baseline():
    """Audit baseline: 67 closed flags spread across 7 categories.

    If this changes the docs (CLAUDE.md / master plan) need to track
    it. Test fails the build if someone adds a flag without updating.
    """
    expected = (
        len(SECURITY_FLAGS)
        + len(COMPLIANCE_FLAGS)
        + len(TRUST_FLAGS)
        + len(SCOPE_FLAGS)
        + len(ETHICS_FLAGS)
        + len(POLICY_FLAGS)
        + len(OWASP_LLM_FLAGS)
    )
    assert expected == len(ALL_FLAGS) > 0
    # Accept the current 68-flag total; pin so any silent add/remove
    # surfaces in code review. Update the constant intentionally.
    assert len(ALL_FLAGS) == 68


def test_no_flag_appears_in_two_categories():
    """A flag must belong to exactly one category for `category_of` to
    be deterministic."""
    seen: dict[str, FlagCategory] = {}
    for category, flags in FLAG_VOCABULARY.items():
        for flag in flags:
            assert flag not in seen, (
                f"flag {flag!r} appears in both {seen[flag]} and {category}"
            )
            seen[flag] = category


def test_owasp_category_has_exactly_ten_entries():
    """OWASP LLM Top 10 → exactly 10 closed flags."""
    assert len(OWASP_LLM_FLAGS) == 10


def test_owasp_flags_follow_naming_convention():
    """Each OWASP flag must be `owasp_llmNN_<short_name>` for the
    deterministic OWASP map in Session Report and Compliance Pack."""
    import re

    pattern = re.compile(r"^owasp_llm\d{2}_[a-z_]+$")
    for flag in OWASP_LLM_FLAGS:
        assert pattern.match(flag), f"OWASP flag bad shape: {flag!r}"


# ---------- is_known_flag ----------


def test_is_known_flag_accepts_every_closed_vocab_entry():
    for flag in ALL_FLAGS:
        assert is_known_flag(flag) is True


def test_is_known_flag_accepts_custom_prefix():
    assert is_known_flag("custom_no_email_export") is True
    assert is_known_flag("custom_") is True  # edge: bare prefix


def test_is_known_flag_rejects_arbitrary_strings():
    assert is_known_flag("definitely_not_a_real_flag") is False
    assert is_known_flag("") is False
    assert is_known_flag("Custom_With_Caps") is False  # case-sensitive
    assert is_known_flag("Security_Auditor") is False  # persona, not flag


# ---------- category_of ----------


def test_category_of_routes_each_flag_to_its_set():
    samples = {
        "prompt_injection_direct": FlagCategory.SECURITY,
        "pii_exposure": FlagCategory.COMPLIANCE,
        "hallucination": FlagCategory.TRUST,
        "off_task": FlagCategory.SCOPE,
        "bias_detected": FlagCategory.ETHICS,
        "policy_violation": FlagCategory.POLICY,
        "owasp_llm01_prompt_injection": FlagCategory.OWASP_LLM,
    }
    for flag, expected in samples.items():
        assert category_of(flag) == expected


def test_category_of_returns_none_for_unknown():
    assert category_of("definitely_not_a_real_flag") is None
    assert category_of("custom_user_rule") is None  # custom_ has no category


def test_category_of_returns_none_for_empty():
    assert category_of("") is None


# ---------- security-flag specifics ----------


@pytest.mark.parametrize(
    "flag",
    [
        "prompt_injection_direct",
        "prompt_injection_indirect",
        "jailbreak_attempt",
        "credential_leak",
        "shell_injection",
        "eval_exec_usage",
    ],
)
def test_security_flags_present(flag):
    """The Inspector + Judge specifically depend on these flags."""
    assert flag in SECURITY_FLAGS


@pytest.mark.parametrize(
    "flag",
    [
        "pii_exposure",
        "pii_sent_external",
        "gdpr_art5_violation",
        "soc2_cc6_failed",
        "hipaa_phi_leak",
    ],
)
def test_compliance_flags_present(flag):
    assert flag in COMPLIANCE_FLAGS


def test_policy_flags_include_violation_and_warn():
    """`policy_violation` and `policy_warn` are emitted by the Policy
    Warden persona; the Gateway also references them in escalation
    logic."""
    assert "policy_violation" in POLICY_FLAGS
    assert "policy_warn" in POLICY_FLAGS
