"""REST endpoints for the Agent Registry (`/v1/agents`).

Powers the dashboard's Agents tab: card grid, detail tabs (Identity,
Sessions & Reports, Red-Team Reports), and the adapter-driven profile
patch path. The Inspector scan trigger (`POST .../scan`) lives in the
Inspector router (Phase 21) because it composes with the scanner.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .inspector.orchestrator import inspect_project
from .models.agent import (
    AgentProfilePatch,
    AgentProfilePatchBroadcast,
    AgentRecord,
    AgentRedTeamRow,
    AgentSessionRow,
    AgentSummary,
)
from .models.inspector import InspectorReport
from .storage.dao import (
    get_agent_record,
    get_agent_snapshot_blob,
    get_latest_inspector_report,
    insert_inspector_report,
    list_agent_redteam_runs,
    list_agent_sessions,
    list_agent_summaries,
    unpack_snapshot_blob,
    update_agent_profile,
)
from .ws_broadcaster import broadcaster

log = logging.getLogger("safer.agents_api")

router = APIRouter(prefix="/v1/agents", tags=["agents"])

SCAN_TIMEOUT_SECONDS = 30.0


class ScanRequest(BaseModel):
    """Optional tuning for a manual project scan."""

    skip_persona_review: bool = False
    active_policies: list[dict] | None = None


@router.get("", response_model=list[AgentSummary])
async def list_agents_endpoint() -> list[AgentSummary]:
    return await list_agent_summaries()


@router.get("/{agent_id}", response_model=AgentRecord)
async def get_agent_endpoint(agent_id: str) -> AgentRecord:
    rec = await get_agent_record(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    return rec


@router.get("/{agent_id}/sessions", response_model=list[AgentSessionRow])
async def list_sessions_endpoint(agent_id: str) -> list[AgentSessionRow]:
    rec = await get_agent_record(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    return await list_agent_sessions(agent_id)


@router.get("/{agent_id}/redteam-reports", response_model=list[AgentRedTeamRow])
async def list_redteam_reports_endpoint(agent_id: str) -> list[AgentRedTeamRow]:
    rec = await get_agent_record(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    return await list_agent_redteam_runs(agent_id)


@router.post("/{agent_id}/scan", response_model=InspectorReport)
async def scan_agent_endpoint(
    agent_id: str, request: ScanRequest | None = None
) -> InspectorReport:
    """Trigger a project-wide Inspector scan on the agent's stored snapshot."""
    rec = await get_agent_record(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    blob = await get_agent_snapshot_blob(agent_id)
    if not blob:
        raise HTTPException(
            status_code=409,
            detail=f"agent {agent_id} has no code snapshot — is the SDK sending on_agent_register?",
        )
    try:
        files = unpack_snapshot_blob(blob)
    except Exception as e:
        log.exception("failed to unpack snapshot for %s", agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"could not unpack code snapshot: {type(e).__name__}: {e}",
        ) from e

    opts = request or ScanRequest()
    try:
        report = await asyncio.wait_for(
            inspect_project(
                agent_id=agent_id,
                files=files,
                system_prompt=rec.system_prompt or "",
                active_policies=opts.active_policies,
                skip_persona_review=opts.skip_persona_review,
            ),
            timeout=SCAN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail=f"Inspector timed out after {SCAN_TIMEOUT_SECONDS}s",
        ) from e

    await insert_inspector_report(report)
    await broadcaster.broadcast(
        {
            "type": "inspector_report_ready",
            "agent_id": agent_id,
            "report_id": report.report_id,
            "risk_score": report.risk_score,
            "risk_level": report.risk_level.value,
            "findings_count": len(report.findings),
        }
    )
    return report


@router.get("/{agent_id}/scan", response_model=InspectorReport)
async def get_latest_scan_endpoint(agent_id: str) -> InspectorReport:
    rec = await get_agent_record(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    data = await get_latest_inspector_report(agent_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"no inspector report yet for {agent_id} — run POST .../scan first",
        )
    return InspectorReport.model_validate(data)


@router.patch("/{agent_id}/profile", response_model=AgentRecord)
async def patch_profile_endpoint(
    agent_id: str, patch: AgentProfilePatch
) -> AgentRecord:
    rec = await get_agent_record(agent_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_id} not found")
    changed = await update_agent_profile(
        agent_id,
        name=patch.name,
        version=patch.version,
        system_prompt=patch.system_prompt,
    )
    if changed:
        frame = AgentProfilePatchBroadcast(agent_id=agent_id, fields=changed)
        await broadcaster.broadcast(frame.model_dump(mode="json"))
    updated = await get_agent_record(agent_id)
    assert updated is not None
    return updated
