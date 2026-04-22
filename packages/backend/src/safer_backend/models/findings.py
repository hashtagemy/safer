"""Finding — a concrete issue discovered by Inspector or Red-Team."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FindingSource(str, Enum):
    INSPECTOR = "inspector"
    RED_TEAM = "red_team"
    JUDGE = "judge"
    GATEWAY = "gateway"


def _finding_id() -> str:
    return f"fnd_{uuid4().hex[:16]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Finding(BaseModel):
    finding_id: str = Field(default_factory=_finding_id)
    agent_id: str
    session_id: str | None = None
    source: FindingSource
    severity: Severity
    category: str = Field(description="Flag category from closed vocabulary")
    flag: str = Field(description="Specific flag from closed vocabulary")
    title: str
    description: str
    evidence: list[str] = Field(default_factory=list)
    reproduction_steps: list[str] = Field(default_factory=list)
    recommended_mitigation: str | None = None
    owasp_id: str | None = Field(
        default=None, description="e.g. owasp_llm01_prompt_injection"
    )
    created_at: datetime = Field(default_factory=_utcnow)
