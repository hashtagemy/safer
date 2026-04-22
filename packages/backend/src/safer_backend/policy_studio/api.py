"""FastAPI router for Policy Studio.

Endpoints:

- `POST /v1/policies/compile`  — NL text → CompiledPolicy (stateless).
- `POST /v1/policies/activate` — persist a CompiledPolicy as an ActivePolicy.
- `GET  /v1/policies`          — list (optionally filtered by agent_id).
- `DELETE /v1/policies/{id}`   — soft-delete (flip active=0).

Activation is a separate step so the UI can show the preview + test cases
and let the user confirm before the policy starts influencing live traffic.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..models.policies import ActivePolicy, CompiledPolicy
from ..storage.dao import deactivate_policy, insert_policy, list_policies
from .compiler import compile_policy

log = logging.getLogger("safer.policy_studio.api")

router = APIRouter(prefix="/v1/policies", tags=["policy_studio"])


class CompileRequest(BaseModel):
    nl_text: str = Field(min_length=1, max_length=4000)


class ActivateRequest(BaseModel):
    compiled: CompiledPolicy
    agent_id: str | None = Field(
        default=None,
        description="Scope to one agent; omit for a global policy.",
    )


class PolicyListResponse(BaseModel):
    policies: list[ActivePolicy]


@router.post("/compile", response_model=CompiledPolicy)
async def compile_endpoint(request: CompileRequest) -> CompiledPolicy:
    try:
        return await compile_policy(request.nl_text)
    except RuntimeError as e:
        # Compiler unavailable (no API key in the environment).
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/activate", response_model=ActivePolicy)
async def activate_endpoint(request: ActivateRequest) -> ActivePolicy:
    policy = ActivePolicy.from_compiled(request.compiled, agent_id=request.agent_id)
    await insert_policy(policy)
    log.info(
        "activated policy id=%s name=%s agent=%s",
        policy.policy_id,
        policy.name,
        policy.agent_id or "GLOBAL",
    )
    return policy


@router.get("", response_model=PolicyListResponse)
async def list_endpoint(
    agent_id: str | None = Query(default=None),
    active_only: bool = Query(default=True),
) -> PolicyListResponse:
    rows = await list_policies(agent_id=agent_id, active_only=active_only)
    return PolicyListResponse(policies=rows)


@router.delete("/{policy_id}", status_code=204)
async def deactivate_endpoint(policy_id: str) -> None:
    ok = await deactivate_policy(policy_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"No active policy with id '{policy_id}'",
        )
    return None
