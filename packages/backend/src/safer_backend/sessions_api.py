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
    parent_session_id: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class ActiveSessionRow(BaseModel):
    """One active (unended) session — feeds the /live card list.

    `recent_hooks` is oldest → newest so the UI can render a left-to-right
    activity bar without reversing it.
    """

    session_id: str
    agent_id: str
    agent_name: str
    started_at: str
    total_steps: int
    last_event_at: str | None = None
    last_event_hook: str | None = None
    last_risk_hint: str | None = None
    recent_hooks: list[str] = Field(default_factory=list)


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
                   s.total_steps, s.total_cost_usd, s.overall_health, s.success,
                   s.parent_session_id
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
            parent_session_id=r[9],
        )
        for r in rows
    ]
    return SessionListResponse(sessions=items)


@router.get("/active", response_model=list[ActiveSessionRow])
async def list_active_sessions(
    limit: int = Query(default=100, ge=1, le=500),
    recent_hook_count: int = Query(default=20, ge=1, le=100),
) -> list[ActiveSessionRow]:
    """Sessions that have started but haven't emitted `on_session_end`.

    Ordered by most-recent-activity first so the /live card list shows
    the liveliest session on top. Each row carries the last N event
    hooks so the UI can draw a horizontal activity bar without a
    second round-trip.
    """
    async with get_db() as db:
        async with db.execute(
            """
            SELECT s.session_id, s.agent_id, a.name, s.started_at, s.total_steps,
                   (SELECT MAX(timestamp) FROM events e WHERE e.session_id = s.session_id)   AS last_event_at,
                   (SELECT hook       FROM events e WHERE e.session_id = s.session_id ORDER BY sequence DESC LIMIT 1) AS last_event_hook,
                   (SELECT risk_hint  FROM events e WHERE e.session_id = s.session_id ORDER BY sequence DESC LIMIT 1) AS last_risk_hint
            FROM sessions s
            JOIN agents a ON s.agent_id = a.agent_id
            WHERE s.ended_at IS NULL
            ORDER BY COALESCE(
                (SELECT MAX(timestamp) FROM events e WHERE e.session_id = s.session_id),
                s.started_at
            ) DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()

        out: list[ActiveSessionRow] = []
        for r in rows:
            async with db.execute(
                """
                SELECT hook FROM events
                WHERE session_id = ?
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (r[0], recent_hook_count),
            ) as cur2:
                recent = [row[0] for row in await cur2.fetchall()]
            recent.reverse()  # oldest → newest for the activity bar
            out.append(
                ActiveSessionRow(
                    session_id=r[0],
                    agent_id=r[1],
                    agent_name=r[2] or r[1],
                    started_at=r[3],
                    total_steps=int(r[4] or 0),
                    last_event_at=r[5],
                    last_event_hook=r[6],
                    last_risk_hint=r[7],
                    recent_hooks=recent,
                )
            )
    return out


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
