"""Quality Reviewer output — one Opus call per session.

Quality is distinct from per-event verdicts. The Reviewer reads the
whole session trace (events + verdicts) and returns an aggregate view:
did the agent actually finish the user's job, did it hallucinate, how
wastefully did it wander, and did its goal drift over time?

The numeric `overall_quality_score` feeds the deterministic aggregator
in session_report/aggregator.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GoalDriftEntry(BaseModel):
    step: int
    summary: str = Field(
        description="One-sentence description of how the goal shifted at this step."
    )


class QualitySummary(BaseModel):
    session_id: str
    agent_id: str

    overall_quality_score: int = Field(
        ge=0,
        le=100,
        description="0 = catastrophic, 100 = clean session. Feeds the aggregator.",
    )
    task_completion: int = Field(
        ge=0,
        le=100,
        description="0 = nothing done, 100 = user's ask fully satisfied.",
    )
    hallucination_summary: str = Field(
        default="",
        description="One-paragraph summary of any unsupported claims.",
    )
    efficiency_report: str = Field(
        default="",
        description="One-paragraph summary of wasted steps / loops.",
    )
    goal_drift_timeline: list[GoalDriftEntry] = Field(default_factory=list)

    # Cost tracking is recorded separately via cost_tracker; these two are
    # kept here for debug / UI display.
    latency_ms: int = 0
    cost_usd: float = 0.0
