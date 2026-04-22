"""Policy Studio models.

A user writes a policy in natural language ("Don't let this agent email
customers outside our domain"). The Policy Compiler (Opus 4.7) turns
that sentence into a `CompiledPolicy`: a deterministic rule the Gateway
engine can evaluate, plus a structured test suite the user can accept
or reject before the policy is activated.

Once activated, the row is persisted as an `ActivePolicy` row and the
Gateway's `load_active_policies` picks it up on the next call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .findings import Severity
from .flags import FlagCategory, is_known_flag


class GuardMode(str, Enum):
    MONITOR = "monitor"
    INTERVENE = "intervene"
    ENFORCE = "enforce"


# Closed whitelist of rule kinds the Gateway engine understands.
# Keep this in lockstep with gateway/policy_engine.evaluate_rule.
ALLOWED_RULE_KINDS: frozenset[str] = frozenset(
    {"pii_guard", "tool_allowlist", "loop_detection", "regex_block"}
)


def _policy_id() -> str:
    return f"pol_{uuid4().hex[:16]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PolicyTestCase(BaseModel):
    """A synthetic event + expected outcome used to sanity-check a policy.

    The compiler produces 1-3 of these so the user can see concrete
    behaviour before activating.
    """

    description: str
    event: dict[str, Any]
    expected_block: bool
    expected_flag: str | None = None


class CompiledPolicy(BaseModel):
    """Output of `compile_policy`. NOT yet persisted."""

    name: str = Field(min_length=1, max_length=80)
    nl_text: str
    rule_json: dict[str, Any]
    code_snippet: str | None = None
    flag_category: FlagCategory
    flag: str = Field(
        description=(
            "Closed-vocabulary flag this policy emits on a hit. Custom "
            "user-defined flags must start with 'custom_'."
        )
    )
    severity: Severity
    guard_mode: GuardMode = GuardMode.INTERVENE
    test_cases: list[PolicyTestCase] = Field(default_factory=list)

    @field_validator("rule_json")
    @classmethod
    def _validate_rule_kind(cls, v: dict[str, Any]) -> dict[str, Any]:
        kind = v.get("kind")
        if kind not in ALLOWED_RULE_KINDS:
            raise ValueError(
                f"Unsupported rule kind '{kind}'. Allowed: {sorted(ALLOWED_RULE_KINDS)}"
            )
        return v

    @field_validator("flag")
    @classmethod
    def _validate_flag(cls, v: str) -> str:
        if not is_known_flag(v):
            raise ValueError(
                f"Unknown flag '{v}'. Must be in the closed vocabulary "
                "or start with 'custom_'."
            )
        return v


class ActivePolicy(BaseModel):
    """Persisted policy — matches the `policies` table row shape."""

    policy_id: str = Field(default_factory=_policy_id)
    agent_id: str | None = Field(
        default=None, description="NULL = global, otherwise scoped to this agent"
    )
    name: str
    nl_text: str
    rule_json: dict[str, Any]
    code_snippet: str | None = None
    flag_category: FlagCategory | None = None
    severity: Severity = Severity.MEDIUM
    guard_mode: GuardMode = GuardMode.INTERVENE
    active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    test_cases: list[PolicyTestCase] = Field(default_factory=list)

    @classmethod
    def from_compiled(
        cls, compiled: CompiledPolicy, *, agent_id: str | None = None
    ) -> "ActivePolicy":
        return cls(
            agent_id=agent_id,
            name=compiled.name,
            nl_text=compiled.nl_text,
            rule_json=compiled.rule_json,
            code_snippet=compiled.code_snippet,
            flag_category=compiled.flag_category,
            severity=compiled.severity,
            guard_mode=compiled.guard_mode,
            test_cases=compiled.test_cases,
        )
