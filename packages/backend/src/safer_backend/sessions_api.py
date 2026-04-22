"""Read-only HTTP endpoints for sessions + events.

These back the dashboard's /sessions list and /sessions/:id detail
pages. The ingestion / WebSocket paths remain authoritative for live
state; this router only serves already-persisted rows.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .storage.db import get_db

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


class SessionListItem(BaseModel):
    session_id: str
    agent_id: str
    agent_name: str
    started_at: str
    ended_at: str | None
    total_steps: int
    total_cost_usd: float
    overall_health: int | None = None
    success: bool = True


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class SessionEvent(BaseModel):
    event_id: str
    session_id: str
    agent_id: str
    sequence: int
    hook: str
    timestamp: str
    risk_hint: str
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionEventsResponse(BaseModel):
    session_id: str
    events: list[SessionEvent]


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> SessionListResponse:
    where = ""
    params: list[Any] = []
    if agent_id:
        where = "WHERE s.agent_id = ?"
        params.append(agent_id)
    async with get_db() as db:
        async with db.execute(
            f"""
            SELECT s.session_id, s.agent_id, a.name, s.started_at, s.ended_at,
                   s.total_steps, s.total_cost_usd, s.overall_health, s.success
            FROM sessions s
            JOIN agents a ON s.agent_id = a.agent_id
            {where}
            ORDER BY s.started_at DESC
            LIMIT ?
            """,
            [*params, limit],
        ) as cur:
            rows = await cur.fetchall()
    items = [
        SessionListItem(
            session_id=r[0],
            agent_id=r[1],
            agent_name=r[2],
            started_at=r[3],
            ended_at=r[4],
            total_steps=int(r[5] or 0),
            total_cost_usd=float(r[6] or 0.0),
            overall_health=int(r[7]) if r[7] is not None else None,
            success=bool(r[8]) if r[8] is not None else True,
        )
        for r in rows
    ]
    return SessionListResponse(sessions=items)


@router.get("/{session_id}/events", response_model=SessionEventsResponse)
async def session_events(
    session_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
) -> SessionEventsResponse:
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"session {session_id} not found"
            )
        async with db.execute(
            """
            SELECT event_id, session_id, agent_id, sequence, hook,
                   timestamp, risk_hint, source, payload_json
            FROM events
            WHERE session_id = ?
            ORDER BY sequence ASC
            LIMIT ?
            """,
            (session_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    events: list[SessionEvent] = []
    for r in rows:
        try:
            payload = json.loads(r[8] or "{}")
        except json.JSONDecodeError:
            payload = {}
        events.append(
            SessionEvent(
                event_id=r[0],
                session_id=r[1],
                agent_id=r[2],
                sequence=int(r[3]),
                hook=r[4],
                timestamp=r[5],
                risk_hint=r[6] or "LOW",
                source=r[7] or "sdk",
                payload=payload,
            )
        )
    return SessionEventsResponse(session_id=session_id, events=events)
