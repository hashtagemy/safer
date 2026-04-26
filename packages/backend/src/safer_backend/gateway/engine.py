"""Gateway pre-call decision engine.

Given an incoming event (before_llm_call / before_tool_use / on_final_output),
returns a Decision: allow / warn / block. Combines PII scanning + policy
evaluation + guard mode logic.

Guard mode semantics:
  monitor    — never blocks unless a hit carries `guard_mode="enforce"`;
               otherwise logs warnings only. Per-policy enforce always
               wins over global monitor — when the user wrote "Block any X"
               in Policy Studio, that policy must block regardless of the
               operator's default guard mode.
  intervene  — block on per-policy enforce hits, on any hit with
               severity=CRITICAL, or on hits whose flag is in the
               escalation set (prompt_injection_*, credential_leak,
               pii_sent_external, cross_tenant_data, hipaa_phi_leak,
               data_exfiltration, eval_exec_usage, shell_injection)
  enforce    — block on any hit with severity>=HIGH
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .policy_engine import PolicyHit, PolicyRule, evaluate_policies, load_active_policies


class GuardMode(str, Enum):
    MONITOR = "monitor"
    INTERVENE = "intervene"
    ENFORCE = "enforce"


@dataclass
class Decision:
    decision: str  # "allow" | "warn" | "block"
    hits: list[PolicyHit] = field(default_factory=list)
    reason: str | None = None
    risk: str = "LOW"  # LOW | MEDIUM | HIGH | CRITICAL

    @property
    def is_block(self) -> bool:
        return self.decision == "block"


# Hits that escalate to block in "intervene" mode regardless of severity.
_ESCALATION_FLAGS = {
    "prompt_injection_direct",
    "prompt_injection_indirect",
    "credential_leak",
    "pii_sent_external",
    "cross_tenant_data",
    "hipaa_phi_leak",
    "data_exfiltration",
    "eval_exec_usage",
    "shell_injection",
    "jailbreak_attempt",
}


def _default_guard_mode() -> GuardMode:
    """Current guard mode — reads the runtime-mutable config, which is
    seeded from `SAFER_GUARD_MODE` at startup and can be changed via
    `PATCH /v1/config`."""
    from ..runtime_config import get_guard_mode

    try:
        return GuardMode(get_guard_mode())
    except ValueError:
        return GuardMode.MONITOR


def _max_risk(hits: list[PolicyHit]) -> str:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    best = "LOW"
    for h in hits:
        if order.get(h.severity, 0) > order.get(best, 0):
            best = h.severity
    return best


def apply_mode(hits: list[PolicyHit], mode: GuardMode) -> Decision:
    if not hits:
        return Decision(decision="allow", hits=[], risk="LOW")

    risk = _max_risk(hits)

    # Per-hit `guard_mode="enforce"` (set by Sonnet when the user
    # writes "Block any X") makes the hit terminal regardless of the
    # global mode — including `monitor`. The user explicitly asked for
    # a block in their policy text, so honoring that contract takes
    # precedence over the operator's default mode. Non-enforce hits
    # still defer to global mode below.
    enforce_hit = next(
        (h for h in hits if getattr(h, "guard_mode", "intervene") == "enforce"),
        None,
    )
    if enforce_hit is not None:
        return Decision(
            decision="block",
            hits=hits,
            reason=enforce_hit.recommended_mitigation or "policy violation",
            risk=risk,
        )

    if mode == GuardMode.MONITOR:
        return Decision(
            decision="warn",
            hits=hits,
            reason="guard_mode=monitor: logged but not enforced",
            risk=risk,
        )

    if mode == GuardMode.INTERVENE:
        any_critical = any(h.severity == "CRITICAL" for h in hits)
        any_escalation = any(h.flag in _ESCALATION_FLAGS for h in hits)
        if any_critical or any_escalation:
            return Decision(
                decision="block",
                hits=hits,
                reason=hits[0].recommended_mitigation or "policy violation",
                risk=risk,
            )
        return Decision(
            decision="warn",
            hits=hits,
            reason=f"{len(hits)} policy hit(s) at {risk} — not escalated",
            risk=risk,
        )

    # ENFORCE
    any_high_or_worse = any(h.severity in ("HIGH", "CRITICAL") for h in hits)
    if any_high_or_worse:
        return Decision(
            decision="block",
            hits=hits,
            reason=hits[0].recommended_mitigation or "policy violation",
            risk=risk,
        )
    return Decision(
        decision="warn",
        hits=hits,
        reason=f"{len(hits)} policy hit(s) at {risk}",
        risk=risk,
    )


async def pre_call_check(
    event: dict[str, Any],
    *,
    agent_id: str | None = None,
    mode: GuardMode | None = None,
    policies: list[PolicyRule] | None = None,
) -> Decision:
    """Run the Gateway check on a single pre-call event."""
    if policies is None:
        policies = await load_active_policies(agent_id)
    hits = evaluate_policies(policies, event)
    chosen_mode = mode or _default_guard_mode()
    return apply_mode(hits, chosen_mode)
