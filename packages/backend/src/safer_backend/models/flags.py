"""Closed-vocabulary flag system.

Every persona verdict MUST use flags from this list — free-form flag
strings are not allowed. A closed vocabulary lets the compliance report
auto-aggregate, regression diffs stay comparable, and the OWASP LLM
mapping derives deterministically.

Flags are grouped by semantic category, not by persona. Multiple
personas may emit the same flag (e.g. `pii_exposure` can come from
Compliance Officer AND Security Auditor on the same event).
"""

from __future__ import annotations

from enum import Enum


class FlagCategory(str, Enum):
    SECURITY = "SECURITY"
    COMPLIANCE = "COMPLIANCE"
    TRUST = "TRUST"
    SCOPE = "SCOPE"
    ETHICS = "ETHICS"
    POLICY = "POLICY"
    OWASP_LLM = "OWASP_LLM"


# ---------- SECURITY ----------
SECURITY_FLAGS: set[str] = {
    "prompt_injection_direct",
    "prompt_injection_indirect",
    "jailbreak_attempt",
    "credential_leak",
    "credential_hardcoded",
    "insecure_crypto",
    "eval_exec_usage",
    "insecure_deserialization",
    "shell_injection",
    "ssl_bypass",
    "sql_injection",
    "path_traversal",
    "ssrf_risk",
    "xss_risk",
    "tool_abuse",
    "unauthorized_tool_call",
    "prompt_extraction",
    "data_exfiltration",
}

# ---------- COMPLIANCE ----------
COMPLIANCE_FLAGS: set[str] = {
    "pii_exposure",
    "pii_stored_insecure",
    "pii_logged",
    "pii_sent_external",
    "cross_tenant_data",
    "gdpr_art5_violation",
    "gdpr_art6_violation",
    "gdpr_art32_violation",
    "soc2_cc6_failed",
    "soc2_cc7_failed",
    "hipaa_phi_leak",
    "consent_missing",
    "retention_violation",
    "audit_trail_gap",
    "data_residency_violation",
}

# ---------- TRUST (Hallucination / Honesty) ----------
TRUST_FLAGS: set[str] = {
    "hallucination",
    "unsupported_claim",
    "fabricated_evidence",
    "contradiction",
    "false_success",
    "missing_citation",
    "unverified_fact",
}

# ---------- SCOPE / GOAL DRIFT ----------
SCOPE_FLAGS: set[str] = {
    "off_task",
    "goal_drift",
    "unnecessary_step",
    "loop_detected",
    "scope_creep",
    "excessive_retries",
    "tool_misuse",
}

# ---------- ETHICS ----------
ETHICS_FLAGS: set[str] = {
    "bias_detected",
    "toxic_output",
    "unfair_decision",
    "discriminatory_pattern",
    "harmful_content",
    "misleading_claim",
}

# ---------- POLICY (custom user rules) ----------
POLICY_FLAGS: set[str] = {
    "policy_violation",
    "policy_warn",
    "policy_bypass_attempted",
    "user_rule_breached",
    "config_mismatch",
    # Custom user-compiled policies append their own flags at runtime;
    # see policy_studio.compiler. Those start with `custom_`.
}

# ---------- OWASP LLM Top 10 (2025 edition mapping) ----------
OWASP_LLM_FLAGS: set[str] = {
    "owasp_llm01_prompt_injection",
    "owasp_llm02_insecure_output_handling",
    "owasp_llm03_training_data_poisoning",
    "owasp_llm04_model_denial_of_service",
    "owasp_llm05_supply_chain",
    "owasp_llm06_sensitive_info_disclosure",
    "owasp_llm07_insecure_plugin_design",
    "owasp_llm08_excessive_agency",
    "owasp_llm09_overreliance",
    "owasp_llm10_model_theft",
}


FLAG_VOCABULARY: dict[FlagCategory, set[str]] = {
    FlagCategory.SECURITY: SECURITY_FLAGS,
    FlagCategory.COMPLIANCE: COMPLIANCE_FLAGS,
    FlagCategory.TRUST: TRUST_FLAGS,
    FlagCategory.SCOPE: SCOPE_FLAGS,
    FlagCategory.ETHICS: ETHICS_FLAGS,
    FlagCategory.POLICY: POLICY_FLAGS,
    FlagCategory.OWASP_LLM: OWASP_LLM_FLAGS,
}


ALL_FLAGS: set[str] = set().union(*FLAG_VOCABULARY.values())


def is_known_flag(flag: str) -> bool:
    """True if flag is in closed vocabulary OR starts with `custom_` (user-defined)."""
    return flag in ALL_FLAGS or flag.startswith("custom_")


def category_of(flag: str) -> FlagCategory | None:
    """Return the category of a flag, or None if unknown."""
    for category, flags in FLAG_VOCABULARY.items():
        if flag in flags:
            return category
    return None
