"""Inspector models — onboarding-phase static code review.

Inspector runs AST + deterministic patterns + a 3-persona Judge call
(Security Auditor, Compliance Officer, Policy Warden) in INSPECTOR mode
against agent source code, system prompt, and tool definitions. The
result is an `InspectorReport` combining all three layers into a single
risk picture with actionable policy suggestions.

Design notes:
- `ToolSpec` carries enough structure for the classifier without
  requiring import of the agent's code.
- `PatternMatch` is the raw output of a single deterministic rule and
  is converted into a `Finding` by the orchestrator so compliance /
  reports can treat all findings uniformly.
- `PolicySuggestion` is pre-Policy-Studio text; the user will later
  compile it through Policy Studio (Phase 9).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from .findings import Finding, Severity
from .verdicts import PersonaName, PersonaVerdict


def _suggestion_id() -> str:
    return f"sug_{uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolRiskClass(str, Enum):
    """Deterministic risk bucket for a tool based on its capability."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ToolSpec(BaseModel):
    """A tool declared by an agent (extracted from source or passed in)."""

    name: str
    signature: str = Field(
        default="", description="Python signature e.g. 'send_email(to: str, body: str)'"
    )
    docstring: str | None = None
    decorators: list[str] = Field(default_factory=list)
    risk_class: ToolRiskClass = ToolRiskClass.LOW
    risk_reason: str = Field(
        default="", description="Why the classifier picked this risk class"
    )
    file_path: str | None = None


class LLMCallSite(BaseModel):
    """A call site where the agent talks to an LLM."""

    provider: str = Field(description="e.g. 'anthropic', 'openai', 'unknown'")
    function: str = Field(description="Full dotted call, e.g. 'client.messages.create'")
    line: int = 0
    file_path: str | None = None


class ASTSummary(BaseModel):
    """Deterministic structural facts about the agent source."""

    module: str = ""
    tools: list[ToolSpec] = Field(default_factory=list)
    llm_calls: list[LLMCallSite] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    loc: int = 0
    parse_error: str | None = None


class PatternMatch(BaseModel):
    """A hit from a single deterministic pattern rule."""

    rule_id: str = Field(description="Stable ID, e.g. 'hardcoded_credential'")
    severity: Severity
    flag: str = Field(description="Closed-vocabulary flag, e.g. 'credential_hardcoded'")
    line: int = 0
    snippet: str = ""
    message: str = ""
    file_path: str | None = None


class PolicySuggestion(BaseModel):
    """Recommended user policy derived from Inspector findings."""

    suggestion_id: str = Field(default_factory=_suggestion_id)
    name: str = Field(description="Short human label, e.g. 'credential-redaction'")
    reason: str = Field(description="Which finding/flag motivated this suggestion")
    natural_language: str = Field(
        description="One-sentence policy the user can feed into Policy Studio"
    )
    triggering_flags: list[str] = Field(default_factory=list)
    severity: Severity = Severity.MEDIUM


class InspectorReport(BaseModel):
    """Combined output of an Inspector scan."""

    report_id: str = Field(default_factory=lambda: f"ins_{uuid4().hex[:16]}")
    agent_id: str
    created_at: datetime = Field(default_factory=_utcnow)

    risk_score: int = Field(
        ge=0,
        le=100,
        description="0 = catastrophic, 100 = completely safe",
    )
    risk_level: Severity = Severity.LOW

    ast_summary: ASTSummary
    pattern_matches: list[PatternMatch] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    persona_verdicts: dict[PersonaName, PersonaVerdict] = Field(default_factory=dict)
    policy_suggestions: list[PolicySuggestion] = Field(default_factory=list)

    scan_mode: str = Field(
        default="single", description="'single' for one-file scans, 'project' for multi-file"
    )
    scanned_files: list[str] = Field(default_factory=list)

    duration_ms: int = 0
    persona_review_skipped: bool = False
    persona_review_error: str | None = None
    persona_review_mode: str | None = Field(
        default=None,
        description="'managed' for Claude Managed Agents, 'subagent' for the legacy single-call path, 'managed_fallback_subagent' when managed failed and fell back.",
    )
