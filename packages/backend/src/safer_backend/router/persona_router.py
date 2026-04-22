"""Deterministic persona selection.

6 personas × event = would be wasteful and wrong. Not every persona cares
about every hook. We pre-select which personas evaluate each event in a
single Opus call (see CLAUDE.md for design rationale).

| Hook               | Base personas                                           | + on risk_hint       |
|--------------------|---------------------------------------------------------|----------------------|
| before_tool_use    | Security, Compliance, Scope, Policy Warden              | —                    |
| on_agent_decision  | Scope, Policy Warden                                    | + Security, Trust    |
| on_final_output    | ALL 6                                                   | —                    |
| others             | (no Judge call)                                         | —                    |

Also exposes which personas we activate during Inspector (code scan) mode.
"""

from __future__ import annotations

from ..models.verdicts import PersonaName
from safer.events import Hook, RiskHint

# ---------- Runtime routing ----------

_JUDGE_ELIGIBLE_HOOKS: set[Hook] = {
    Hook.BEFORE_TOOL_USE,
    Hook.ON_AGENT_DECISION,
    Hook.ON_FINAL_OUTPUT,
}


def is_judge_eligible(hook: Hook | str) -> bool:
    h = Hook(hook) if isinstance(hook, str) else hook
    return h in _JUDGE_ELIGIBLE_HOOKS


def select_personas_runtime(
    hook: Hook | str,
    risk_hint: RiskHint | str = RiskHint.LOW,
) -> list[PersonaName]:
    """Pick the persona set for a single runtime event."""
    h = Hook(hook) if isinstance(hook, str) else hook
    r = RiskHint(risk_hint) if isinstance(risk_hint, str) else risk_hint
    risk_elevated = r in (RiskHint.MEDIUM, RiskHint.HIGH, RiskHint.CRITICAL)

    if h == Hook.BEFORE_TOOL_USE:
        return [
            PersonaName.SECURITY_AUDITOR,
            PersonaName.COMPLIANCE_OFFICER,
            PersonaName.SCOPE_ENFORCER,
            PersonaName.POLICY_WARDEN,
        ]

    if h == Hook.ON_AGENT_DECISION:
        base: list[PersonaName] = [
            PersonaName.SCOPE_ENFORCER,
            PersonaName.POLICY_WARDEN,
        ]
        if risk_elevated:
            base.append(PersonaName.SECURITY_AUDITOR)
            base.append(PersonaName.TRUST_GUARDIAN)
        return base

    if h == Hook.ON_FINAL_OUTPUT:
        return [
            PersonaName.SECURITY_AUDITOR,
            PersonaName.COMPLIANCE_OFFICER,
            PersonaName.TRUST_GUARDIAN,
            PersonaName.SCOPE_ENFORCER,
            PersonaName.ETHICS_REVIEWER,
            PersonaName.POLICY_WARDEN,
        ]

    return []


# ---------- Inspector routing (code scan) ----------

INSPECTOR_PERSONAS: list[PersonaName] = [
    PersonaName.SECURITY_AUDITOR,
    PersonaName.COMPLIANCE_OFFICER,
    PersonaName.POLICY_WARDEN,
]


def select_personas_inspector() -> list[PersonaName]:
    return list(INSPECTOR_PERSONAS)
