"""Minimal DAO helpers: upsert agent, ensure session, insert event.

Append-only events; agents/sessions are upserted (idempotent on id).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from safer.events import Event

from .db import get_db


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def upsert_agent(
    agent_id: str,
    name: str | None = None,
    framework: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO agents (agent_id, name, framework, created_at, last_seen_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                name = COALESCE(excluded.name, agents.name),
                framework = COALESCE(excluded.framework, agents.framework),
                last_seen_at = excluded.last_seen_at
            """,
            (
                agent_id,
                name or agent_id,
                framework,
                _utcnow_iso(),
                _utcnow_iso(),
                json.dumps(metadata or {}),
            ),
        )
        await db.commit()


async def ensure_session(session_id: str, agent_id: str) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO sessions (session_id, agent_id, started_at)
            VALUES (?, ?, ?)
            """,
            (session_id, agent_id, _utcnow_iso()),
        )
        await db.commit()


async def insert_event(event: Event) -> None:
    """Insert a validated event. Upserts the agent and session if needed."""
    await upsert_agent(event.agent_id)
    await ensure_session(event.session_id, event.agent_id)
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO events
            (event_id, session_id, agent_id, sequence, hook, timestamp, risk_hint, source, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.session_id,
                event.agent_id,
                event.sequence,
                event.hook.value,
                event.timestamp.isoformat(),
                event.risk_hint.value,
                event.source,
                event.model_dump_json(),
            ),
        )
        # Close session on on_session_end
        if event.hook.value == "on_session_end":
            await db.execute(
                """
                UPDATE sessions
                SET ended_at = ?
                WHERE session_id = ?
                """,
                (event.timestamp.isoformat(), event.session_id),
            )
        await db.commit()


async def get_stats() -> dict[str, Any]:
    """Counts for /v1/stats."""
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM agents") as cur:
            (agents_count,) = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM sessions") as cur:
            (sessions_count,) = await cur.fetchone()
        async with db.execute(
            "SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL"
        ) as cur:
            (active_sessions,) = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM events") as cur:
            (events_count,) = await cur.fetchone()
    return {
        "agents": agents_count,
        "sessions": sessions_count,
        "active_sessions": active_sessions,
        "events": events_count,
    }


async def get_cost_summary() -> dict[str, Any]:
    """Counts for /v1/stats/cost — total spent, today, by component."""
    async with get_db() as db:
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0), COUNT(*) FROM claude_calls"
        ) as cur:
            total_usd, total_calls = await cur.fetchone()
        today_iso = datetime.now(timezone.utc).date().isoformat()
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM claude_calls WHERE DATE(timestamp) = ?",
            (today_iso,),
        ) as cur:
            (today_usd,) = await cur.fetchone()
        async with db.execute(
            """
            SELECT component, COALESCE(SUM(cost_usd), 0.0) AS cost
            FROM claude_calls
            GROUP BY component
            """
        ) as cur:
            by_component = {row[0]: row[1] for row in await cur.fetchall()}
    return {
        "total_usd": float(total_usd),
        "today_usd": float(today_usd),
        "total_calls": int(total_calls),
        "by_component": by_component,
    }
