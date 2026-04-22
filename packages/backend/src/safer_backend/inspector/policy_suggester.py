"""Deterministic flag → policy suggestion mapping.

Given the flags emitted by the 3-persona Inspector review AND the
deterministic pattern rules, pick the user-facing policies that would
plausibly prevent or catch each class of issue. The natural-language
strings here are short enough that the user can paste them into the
Policy Studio (Phase 9) prompt to compile an enforceable rule.

The mapping is intentionally conservative: one flag can motivate
multiple policies, and multiple flags coalesce into one policy
suggestion (de-duplicated by `name`). Severity rolls up to the highest
seen among triggering flags.
"""

from __future__ import annotations

from ..models.findings import Severity
from ..models.inspector import PatternMatch, PolicySuggestion
from ..models.verdicts import PersonaVerdict


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


# (policy_name, natural_language, default_severity) for each flag.
_FLAG_TO_POLICY: dict[str, tuple[str, str, Severity]] = {
    # --- Security (credentials / code exec) ---
    "credential_hardcoded": (
        "credential-redaction",
        "Block any tool call or LLM response that contains a value matching "
        "a credential pattern (sk-, sk-ant-, AKIA, ghp_, xoxb-, PRIVATE KEY).",
        Severity.CRITICAL,
    ),
    "credential_leak": (
        "credential-redaction",
        "Redact any API key, token, or private key before it leaves the agent.",
        Severity.CRITICAL,
    ),
    "eval_exec_usage": (
        "code-execution-block",
        "Refuse any action that ends up calling eval, exec, or a raw subprocess "
        "command unless the tool is explicitly listed as a code sandbox.",
        Severity.CRITICAL,
    ),
    "shell_injection": (
        "code-execution-block",
        "Block shell commands that concatenate user input; require argv-list form.",
        Severity.CRITICAL,
    ),
    "insecure_deserialization": (
        "deserialization-guard",
        "Disallow pickle.loads / marshal.loads / unsafe yaml.load on any data "
        "that did not originate inside this process.",
        Severity.HIGH,
    ),
    "ssl_bypass": (
        "tls-enforcement",
        "Require TLS verification on every outbound HTTP call; block http:// "
        "endpoints except localhost.",
        Severity.HIGH,
    ),
    "sql_injection": (
        "sql-param-required",
        "All SQL queries must use parameterized statements; block execute() with "
        "string-concat or f-string arguments.",
        Severity.HIGH,
    ),
    "path_traversal": (
        "path-sanitize",
        "Reject file paths containing '..' or absolute paths outside the agent's "
        "declared workspace.",
        Severity.MEDIUM,
    ),
    "insecure_crypto": (
        "crypto-standards",
        "Require SHA-256 or stronger for security use; flag md5/sha1 in source.",
        Severity.MEDIUM,
    ),
    # --- Prompt injection / tool abuse ---
    "prompt_injection_direct": (
        "prompt-injection-filter",
        "Detect and block messages that try to override the system prompt "
        "('ignore previous instructions', role override).",
        Severity.HIGH,
    ),
    "prompt_injection_indirect": (
        "prompt-injection-filter",
        "Treat content pulled from tool results as untrusted; sanitize before "
        "feeding it back into the LLM.",
        Severity.HIGH,
    ),
    "prompt_extraction": (
        "system-prompt-confidentiality",
        "Block responses that reveal the agent's own system prompt or tool "
        "definitions.",
        Severity.HIGH,
    ),
    "tool_abuse": (
        "tool-allowlist",
        "Restrict this agent to an allowlist of tools approved for its role.",
        Severity.HIGH,
    ),
    "unauthorized_tool_call": (
        "tool-allowlist",
        "Block tool calls to tools outside the agent's declared capability set.",
        Severity.HIGH,
    ),
    "data_exfiltration": (
        "egress-allowlist",
        "Block outbound calls to destinations not on the agent's egress "
        "allowlist.",
        Severity.CRITICAL,
    ),
    # --- Compliance / PII ---
    "pii_exposure": (
        "pii-egress-block",
        "Block any tool call or response that carries personal data "
        "(email, phone, SSN, TCKN, credit card) to a non-allowlisted destination.",
        Severity.HIGH,
    ),
    "pii_sent_external": (
        "pii-egress-block",
        "Prevent personal data from being sent to any endpoint that is not "
        "explicitly marked as a trusted PII processor.",
        Severity.CRITICAL,
    ),
    "pii_logged": (
        "pii-log-masking",
        "Mask personal data before writing anything to logs or traces.",
        Severity.HIGH,
    ),
    "pii_stored_insecure": (
        "pii-at-rest",
        "Require encryption for any persistence layer that stores personal data.",
        Severity.HIGH,
    ),
    "cross_tenant_data": (
        "tenant-isolation",
        "Reject tool calls that read data belonging to a tenant other than the "
        "one the session is scoped to.",
        Severity.CRITICAL,
    ),
    "consent_missing": (
        "consent-gate",
        "Require explicit consent record before processing personal data under "
        "GDPR Art. 6.",
        Severity.MEDIUM,
    ),
    "audit_trail_gap": (
        "audit-trail",
        "Log every data-access tool call with actor, target, and timestamp.",
        Severity.MEDIUM,
    ),
    # --- Behavior / scope ---
    "loop_detected": (
        "loop-detection",
        "Flag when the same tool is called with near-identical args more than "
        "three times in a row (already a built-in policy — keep enabled).",
        Severity.LOW,
    ),
    "off_task": (
        "scope-guard",
        "Block tool calls that are not reasonably connected to the user's "
        "stated goal.",
        Severity.MEDIUM,
    ),
    "scope_creep": (
        "scope-guard",
        "Warn when the agent begins work outside its declared domain.",
        Severity.MEDIUM,
    ),
    # --- Misc config ---
    "config_mismatch": (
        "config-audit",
        "Ensure production deployments never run with debug=True or other "
        "dev-only flags.",
        Severity.LOW,
    ),
}


def _merge(
    suggestions: dict[str, PolicySuggestion],
    *,
    name: str,
    natural_language: str,
    severity: Severity,
    flag: str,
) -> None:
    existing = suggestions.get(name)
    if existing is None:
        suggestions[name] = PolicySuggestion(
            name=name,
            reason=f"Triggered by flag '{flag}'",
            natural_language=natural_language,
            triggering_flags=[flag],
            severity=severity,
        )
        return
    if flag not in existing.triggering_flags:
        existing.triggering_flags.append(flag)
    # Bump severity if this flag is more severe than the stored one.
    if _SEVERITY_ORDER[severity] > _SEVERITY_ORDER[existing.severity]:
        existing.severity = severity
    # Reason stays simple — list the primary flags.
    existing.reason = "Triggered by flags: " + ", ".join(sorted(existing.triggering_flags))


def suggest_policies(
    *,
    persona_verdicts: dict[str, PersonaVerdict] | None = None,
    pattern_matches: list[PatternMatch] | None = None,
) -> list[PolicySuggestion]:
    """Return deterministic policy suggestions for the observed flags.

    Accepts both Inspector layers (persona verdicts + pattern matches) and
    deduplicates so each policy is suggested once even if multiple flags
    trigger it.
    """
    suggestions: dict[str, PolicySuggestion] = {}

    if persona_verdicts:
        for verdict in persona_verdicts.values():
            for flag in verdict.flags:
                entry = _FLAG_TO_POLICY.get(flag)
                if entry is None:
                    continue
                name, nl, default_sev = entry
                _merge(
                    suggestions,
                    name=name,
                    natural_language=nl,
                    severity=default_sev,
                    flag=flag,
                )

    if pattern_matches:
        for match in pattern_matches:
            entry = _FLAG_TO_POLICY.get(match.flag)
            if entry is None:
                continue
            name, nl, default_sev = entry
            # For pattern matches, the rule's own severity is authoritative.
            sev = (
                match.severity
                if _SEVERITY_ORDER[match.severity] > _SEVERITY_ORDER[default_sev]
                else default_sev
            )
            _merge(
                suggestions,
                name=name,
                natural_language=nl,
                severity=sev,
                flag=match.flag,
            )

    # Sort: highest severity first, then alphabetical by name.
    return sorted(
        suggestions.values(),
        key=lambda s: (-_SEVERITY_ORDER[s.severity], s.name),
    )
