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
    agent_id: str | None = Field(
        default=None,
        description=(
            "Optional target agent. When provided, the compiler is given "
            "that agent's tool surface (tool names + observed argument "
            "keys) so it can bind the rule to the agent's real schema "
            "instead of guessing field names."
        ),
    )


class ActivateRequest(BaseModel):
    compiled: CompiledPolicy
    agent_id: str | None = Field(
        default=None,
        description=(
            "Deprecated single-agent scope. Prefer `agent_ids`. Used "
            "only when `agent_ids` is empty/omitted."
        ),
    )
    agent_ids: list[str] = Field(
        default_factory=list,
        description=(
            "One or more target agents. Each gets its own ActivePolicy "
            "row sharing the same compiled rule. Empty list (and no "
            "`agent_id`) means a global policy."
        ),
    )


class ActivateResponse(BaseModel):
    policies: list[ActivePolicy]


class PolicyListResponse(BaseModel):
    policies: list[ActivePolicy]


@router.post("/compile", response_model=CompiledPolicy)
async def compile_endpoint(request: CompileRequest) -> CompiledPolicy:
    try:
        return await compile_policy(
            request.nl_text, agent_id=request.agent_id
        )
    except RuntimeError as e:
        # Compiler unavailable (no API key in the environment).
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/activate", response_model=ActivateResponse)
async def activate_endpoint(request: ActivateRequest) -> ActivateResponse:
    targets: list[str | None]
    if request.agent_ids:
        targets = list(request.agent_ids)
    elif request.agent_id is not None:
        targets = [request.agent_id]
    else:
        targets = [None]  # global

    created: list[ActivePolicy] = []
    for target in targets:
        policy = ActivePolicy.from_compiled(request.compiled, agent_id=target)
        await insert_policy(policy)
        log.info(
            "activated policy id=%s name=%s agent=%s",
            policy.policy_id,
            policy.name,
            policy.agent_id or "GLOBAL",
        )
        created.append(policy)
    return ActivateResponse(policies=created)


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
