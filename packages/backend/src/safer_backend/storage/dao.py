"""Minimal DAO helpers: upsert agent, ensure session, insert event.

Append-only events; agents/sessions are upserted (idempotent on id).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from safer.events import Event

from .db import get_db

if TYPE_CHECKING:
    from ..models.policies import ActivePolicy


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


async def insert_policy(policy: "ActivePolicy") -> None:
    """Persist a compiled policy. `active=1` by default (activate on insert)."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO policies
            (policy_id, agent_id, name, nl_text, rule_json, code_snippet,
             flag_category, severity, active, guard_mode, created_at, test_cases_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy.policy_id,
                policy.agent_id,
                policy.name,
                policy.nl_text,
                json.dumps(policy.rule_json),
                policy.code_snippet,
                policy.flag_category.value if policy.flag_category else None,
                policy.severity.value,
                1 if policy.active else 0,
                policy.guard_mode.value,
                policy.created_at.isoformat(),
                json.dumps(
                    [tc.model_dump(mode="json") for tc in policy.test_cases]
                ),
            ),
        )
        await db.commit()


async def list_policies(
    agent_id: str | None = None, active_only: bool = True
) -> list["ActivePolicy"]:
    """Return policies, optionally filtered to `agent_id` and active rows."""
    from ..models.policies import ActivePolicy, GuardMode, PolicyTestCase
    from ..models.findings import Severity
    from ..models.flags import FlagCategory

    clauses: list[str] = []
    params: list[Any] = []
    if active_only:
        clauses.append("active = 1")
    if agent_id is not None:
        clauses.append("(agent_id IS NULL OR agent_id = ?)")
        params.append(agent_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    async with get_db() as db:
        async with db.execute(
            f"""
            SELECT policy_id, agent_id, name, nl_text, rule_json, code_snippet,
                   flag_category, severity, active, guard_mode, created_at,
                   test_cases_json
            FROM policies
            {where}
            ORDER BY created_at DESC
            """,
            params,
        ) as cur:
            rows = await cur.fetchall()

    out: list[ActivePolicy] = []
    for row in rows:
        try:
            test_cases_raw = json.loads(row[11] or "[]")
            test_cases = [PolicyTestCase.model_validate(tc) for tc in test_cases_raw]
        except Exception:
            test_cases = []
        out.append(
            ActivePolicy(
                policy_id=row[0],
                agent_id=row[1],
                name=row[2],
                nl_text=row[3],
                rule_json=json.loads(row[4] or "{}"),
                code_snippet=row[5],
                flag_category=FlagCategory(row[6]) if row[6] else None,
                severity=Severity(row[7]) if row[7] else Severity.MEDIUM,
                active=bool(row[8]),
                guard_mode=GuardMode(row[9]) if row[9] else GuardMode.INTERVENE,
                created_at=datetime.fromisoformat(row[10]),
                test_cases=test_cases,
            )
        )
    return out


async def deactivate_policy(policy_id: str) -> bool:
    """Flip `active=0` on the given policy. Returns True if a row changed."""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE policies SET active = 0 WHERE policy_id = ? AND active = 1",
            (policy_id,),
        )
        await db.commit()
        return cur.rowcount > 0


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
