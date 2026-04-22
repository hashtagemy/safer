"""Thought-Chain Reconstructor output.

Narrative + structured timeline the dashboard streams into the Session
Detail page. Reuses `TimelineEntry` from session_report so the session
report card and the narrative timeline share the same shape.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .session_report import TimelineEntry


class ThoughtChain(BaseModel):
    session_id: str
    agent_id: str
    narrative: str = Field(
        description="Short story of what the agent did, for a human reader."
    )
    timeline: list[TimelineEntry] = Field(default_factory=list)

    latency_ms: int = 0
    cost_usd: float = 0.0
