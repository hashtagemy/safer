"""Judge verdict models.

Verdict is the structured output of a Multi-Persona Judge call.
Dynamic routing (see CLAUDE.md) decides which personas are active
for a given event; the verdict contains entries only for active personas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class PersonaName(str, Enum):
    SECURITY_AUDITOR = "security_auditor"
    COMPLIANCE_OFFICER = "compliance_officer"
    TRUST_GUARDIAN = "trust_guardian"
    SCOPE_ENFORCER = "scope_enforcer"
    ETHICS_REVIEWER = "ethics_reviewer"
    POLICY_WARDEN = "policy_warden"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PersonaVerdict(BaseModel):
    """Single persona's evaluation of an event.

    `score` — 0 (worst) to 100 (best) from this persona's perspective.
    `confidence` — 0.0 to 1.0.
    `flags` — closed-vocabulary flags (see flags.py). Evidence-backed.
    `evidence` — direct quotes from the event payload supporting the flags.
    """

    persona: PersonaName
    score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(
        default_factory=list,
        description="Direct quotes from event payload supporting the verdict",
    )
    reasoning: str = ""
    recommended_mitigation: str | None = None

    @field_validator("flags")
    @classmethod
    def _validate_flags_known(cls, v: list[str]) -> list[str]:
        # Import locally to avoid circular import at module load time.
        from .flags import is_known_flag

        unknown = [f for f in v if not is_known_flag(f)]
        if unknown:
            raise ValueError(f"Unknown flags (not in closed vocabulary): {unknown}")
        return v


class Overall(BaseModel):
    """Aggregated judgment across active personas."""

    risk: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    block: bool = False


def _verdict_id() -> str:
    return f"vdt_{uuid4().hex[:16]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Verdict(BaseModel):
    """Full Judge output for a single event."""

    verdict_id: str = Field(default_factory=_verdict_id)
    event_id: str
    session_id: str
    agent_id: str
    timestamp: datetime = Field(default_factory=_utcnow)
    mode: str = Field(
        default="RUNTIME",
        description="RUNTIME for live Judge, INSPECTOR for code scan",
    )
    active_personas: list[PersonaName]
    personas: dict[PersonaName, PersonaVerdict] = Field(default_factory=dict)
    overall: Overall
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    # Opaque extra fields for debug (not persisted as columns)
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
