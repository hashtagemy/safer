"""FastAPI router for Red-Team endpoints.

- `POST /v1/agents/{agent_id}/redteam/run`  — kicks off a run; returns the
  final RedTeamRun synchronously. (Phase events still stream over
  `/ws/stream` via the broadcaster.)
- `GET  /v1/redteam/runs/{run_id}`          — load a persisted run.
- `GET  /v1/agents/{agent_id}/redteam/runs` — latest N runs for an agent.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..models.redteam import (
    AttackSpec,
    Attempt,
    RedTeamMode,
    RedTeamPhase,
    RedTeamRun,
)
from ..storage.db import get_db
from .orchestrator import run_redteam

router = APIRouter(tags=["redteam"])


class RunRequest(BaseModel):
    target_system_prompt: str = Field(min_length=1)
    target_tools: list[str] = Field(default_factory=list)
    target_name: str = ""
    num_attacks: int = Field(default=10, ge=1, le=30)
    mode: Literal["managed", "subagent"] | None = None


@router.post("/v1/agents/{agent_id}/redteam/run", response_model=RedTeamRun)
async def kickoff(agent_id: str, request: RunRequest) -> RedTeamRun:
    # Verify the agent exists; we auto-create one on ingestion otherwise.
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        from ..storage.dao import upsert_agent

        await upsert_agent(agent_id, name=request.target_name or agent_id)

    mode: RedTeamMode | None = None
    if request.mode:
        mode = RedTeamMode(request.mode)

    run = await run_redteam(
        agent_id=agent_id,
        target_system_prompt=request.target_system_prompt,
        target_tools=request.target_tools,
        target_name=request.target_name,
        num_attacks=request.num_attacks,
        mode=mode,
    )
    return run


@router.get("/v1/redteam/runs/{run_id}", response_model=RedTeamRun)
async def get_run(run_id: str) -> RedTeamRun:
    return await _load_run(run_id)


@router.get("/v1/agents/{agent_id}/redteam/runs", response_model=list[RedTeamRun])
async def list_runs(
    agent_id: str, limit: int = Query(default=10, ge=1, le=50)
) -> list[RedTeamRun]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT run_id
            FROM red_team_runs
            WHERE agent_id = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [await _load_run(r[0]) for r in rows]


async def _load_run(run_id: str) -> RedTeamRun:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT run_id, agent_id, mode, phase, started_at, finished_at,
                   attack_specs_json, attempts_json, findings_count, safety_score,
                   owasp_map_json, error
            FROM red_team_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No run with id '{run_id}'")

    from datetime import datetime

    attack_specs: list[AttackSpec] = []
    try:
        for e in json.loads(row[6] or "[]"):
            try:
                attack_specs.append(AttackSpec.model_validate(e))
            except Exception:
                continue
    except json.JSONDecodeError:
        pass

    attempts: list[Attempt] = []
    try:
        for e in json.loads(row[7] or "[]"):
            try:
                attempts.append(Attempt.model_validate(e))
            except Exception:
                continue
    except json.JSONDecodeError:
        pass

    try:
        owasp_map = json.loads(row[10] or "{}")
    except json.JSONDecodeError:
        owasp_map = {}

    return RedTeamRun(
        run_id=row[0],
        agent_id=row[1],
        mode=RedTeamMode(row[2]) if row[2] else RedTeamMode.SUBAGENT,
        phase=RedTeamPhase(row[3]) if row[3] else RedTeamPhase.PLANNING,
        started_at=datetime.fromisoformat(row[4]),
        finished_at=datetime.fromisoformat(row[5]) if row[5] else None,
        attack_specs=attack_specs,
        attempts=attempts,
        findings_count=int(row[8] or 0),
        safety_score=int(row[9] or 0),
        owasp_map={str(k): int(v) for k, v in owasp_map.items()},
        error=row[11],
    )
