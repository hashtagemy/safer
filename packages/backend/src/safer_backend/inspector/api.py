"""FastAPI router exposing the Inspector as `POST /v1/agents/{id}/inspect`."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.inspector import InspectorReport, ToolSpec
from .orchestrator import inspect

router = APIRouter(prefix="/v1/agents", tags=["inspector"])

# Matches the ceiling used by POST /v1/agents/{id}/scan — Managed
# Agents sessions typically need 60-150s end-to-end; sub-agent path
# is fast enough that the higher ceiling is harmless.
INSPECT_TIMEOUT_SECONDS = 300.0


class InspectRequest(BaseModel):
    """Payload for an Inspector scan.

    `source` is the agent's Python source code as a string. The caller
    can optionally provide a curated `declared_tools` list; if omitted,
    the AST scanner's discovered tools are used instead.
    """

    source: str = Field(min_length=1)
    system_prompt: str = ""
    active_policies: list[dict] = Field(default_factory=list)
    declared_tools: list[ToolSpec] | None = None
    module_name: str = ""
    skip_persona_review: bool = False


@router.post("/{agent_id}/inspect", response_model=InspectorReport)
async def inspect_agent(agent_id: str, request: InspectRequest) -> InspectorReport:
    try:
        return await asyncio.wait_for(
            inspect(
                agent_id=agent_id,
                source=request.source,
                system_prompt=request.system_prompt,
                declared_tools=request.declared_tools,
                active_policies=request.active_policies,
                module_name=request.module_name,
                skip_persona_review=request.skip_persona_review,
            ),
            timeout=INSPECT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail=f"Inspector timed out after {INSPECT_TIMEOUT_SECONDS}s",
        ) from e
