"""Session Report — per-session deterministic health card.

Populated by session_report.aggregator from existing verdicts + Quality
Reviewer + Thought-Chain Reconstructor + Red-Team run. Zero additional
Claude calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CategoryScore(BaseModel):
    """One of the 7 category scores on the report card."""

    name: str  # e.g. "security", "compliance", "trust", "scope", "ethics", "policy_warden", "quality"
    value: int = Field(ge=0, le=100)
    flag_count_by_severity: dict[str, int] = Field(
        default_factory=lambda: {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    )


class TimelineEntry(BaseModel):
    step: int
    hook: str
    risk: str
    summary: str


class TopFinding(BaseModel):
    severity: str
    category: str
    flag: str
    summary: str
    step: int | None = None


class CostSummary(BaseModel):
    total_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    num_opus_calls: int = 0
    num_haiku_calls: int = 0


class RedTeamSummary(BaseModel):
    run_id: str
    safety_score: int
    findings_count: int
    ran_at: datetime


class SessionReport(BaseModel):
    session_id: str
    agent_id: str
    agent_name: str
    generated_at: datetime = Field(default_factory=_utcnow)

    # Health
    overall_health: int = Field(ge=0, le=100)
    categories: list[CategoryScore]

    # Findings
    top_findings: list[TopFinding] = Field(default_factory=list)
    owasp_map: dict[str, int] = Field(default_factory=dict)

    # Narrative
    thought_chain_narrative: str | None = None
    timeline: list[TimelineEntry] = Field(default_factory=list)

    # Red-Team link (if this agent has one)
    red_team_summary: RedTeamSummary | None = None

    # Cost
    cost: CostSummary = Field(default_factory=CostSummary)

    # Session meta
    duration_ms: int = 0
    total_steps: int = 0
    success: bool = True

    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
