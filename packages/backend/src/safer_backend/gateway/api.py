"""Synchronous gateway check endpoint.

`POST /v1/gateway/check` lets SDK adapters ask the backend, before they
let a tool/LLM/output go through, whether the call should be blocked.
The body is the same shape as the events the SDK already emits — the
endpoint reuses `pre_call_check` so policy semantics stay in one place.

Adapters call this in addition to (not instead of) the async event
stream: the event stream remains the source of truth for the dashboard
and the Judge; the check endpoint is purely a synchronous control
back-channel that returns a verdict the SDK can act on.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .engine import pre_call_check

log = logging.getLogger("safer.gateway.api")

router = APIRouter(prefix="/v1/gateway", tags=["gateway"])


class GatewayCheckRequest(BaseModel):
    hook: str = Field(..., description="before_tool_use | before_llm_call | on_final_output")
    agent_id: str
    session_id: str | None = None
    tool_name: str | None = None
    args: dict[str, Any] | None = None
    prompt: str | None = None
    response: str | None = None
    final_response: str | None = None


class GatewayHit(BaseModel):
    policy_id: str
    policy_name: str
    severity: str
    flag: str
    evidence: list[str] = []
    guard_mode: str = "intervene"


class GatewayCheckResponse(BaseModel):
    decision: str
    risk: str
    reason: str | None = None
    hits: list[GatewayHit] = []


@router.post("/check", response_model=GatewayCheckResponse)
async def gateway_check(req: GatewayCheckRequest) -> GatewayCheckResponse:
    event = req.model_dump(exclude_none=True)
    decision = await pre_call_check(event, agent_id=req.agent_id)
    return GatewayCheckResponse(
        decision=decision.decision,
        risk=decision.risk,
        reason=decision.reason,
        hits=[
            GatewayHit(
                policy_id=h.policy_id,
                policy_name=h.policy_name,
                severity=h.severity,
                flag=h.flag,
                evidence=list(h.evidence),
                guard_mode=getattr(h, "guard_mode", "intervene"),
            )
            for h in decision.hits
        ],
    )
