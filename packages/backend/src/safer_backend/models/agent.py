"""Pydantic models for the Agent Registry.

`AgentRecord` — full row, used by `GET /v1/agents/{id}` and the
onboarding ingest path. The gzip snapshot blob is kept server-side and
never returned over the wire.

`AgentSummary` — trimmed list item for `GET /v1/agents`: enough for the
dashboard card grid without any heavy fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ScanStatus = Literal["unscanned", "scanning", "scanned"]


class AgentRecord(BaseModel):
    """Full agent row. Mirrors the `agents` table minus the snapshot blob."""

    agent_id: str
    name: str
    framework: str | None = None
    version: str | None = None
    system_prompt: str | None = None
    project_root: str | None = None
    code_snapshot_hash: str | None = None
    file_count: int = 0
    total_bytes: int = 0
    snapshot_truncated: bool = False
    created_at: datetime
    registered_at: datetime | None = None
    last_seen_at: datetime | None = None
    latest_scan_id: str | None = None
    risk_score: int = 0


class AgentSummary(BaseModel):
    """Compact shape for the Agents card grid."""

    agent_id: str
    name: str
    framework: str | None = None
    version: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None
    risk_score: int = 0
    latest_scan_id: str | None = None
    scan_status: ScanStatus = "unscanned"
    file_count: int = 0


class AgentProfilePatch(BaseModel):
    """Partial update for the agent profile (adapter-captured prompt, etc.)."""

    system_prompt: str | None = None
    name: str | None = None
    version: str | None = None


class AgentSessionRow(BaseModel):
    """One row of the 'Sessions & Reports' tab on the agent detail page."""

    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    total_steps: int = 0
    total_cost_usd: float = 0.0
    success: bool = True
    overall_health: int | None = None
    has_report: bool = False


class AgentRedTeamRow(BaseModel):
    """One row of the 'Red-Team Reports' tab on the agent detail page."""

    run_id: str
    mode: str
    phase: str
    started_at: datetime
    finished_at: datetime | None = None
    findings_count: int = 0
    safety_score: int = 0


class AgentRegisterBroadcast(BaseModel):
    """WebSocket frame emitted when a fresh agent registers."""

    type: Literal["agent_registered"] = "agent_registered"
    agent_id: str
    name: str
    framework: str | None = None
    registered_at: datetime
    code_snapshot_hash: str | None = None
    file_count: int = 0


class AgentProfilePatchBroadcast(BaseModel):
    """WebSocket frame emitted on a profile PATCH so the dashboard refetches."""

    type: Literal["agent_profile_patched"] = "agent_profile_patched"
    agent_id: str
    fields: list[str] = Field(default_factory=list)
