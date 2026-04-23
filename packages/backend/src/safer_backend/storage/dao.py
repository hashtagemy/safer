"""Minimal DAO helpers: upsert agent, ensure session, insert event.

Append-only events; agents/sessions are upserted (idempotent on id).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from safer.events import Event

from .db import get_db

if TYPE_CHECKING:
    from ..models.agent import (
        AgentRecord,
        AgentRedTeamRow,
        AgentSessionRow,
        AgentSummary,
    )
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


async def ingest_agent_register(
    *,
    agent_id: str,
    agent_name: str,
    framework: str | None,
    version: str | None,
    system_prompt: str | None,
    project_root: str | None,
    code_snapshot_b64: str,
    code_snapshot_hash: str,
    file_count: int,
    total_bytes: int,
    truncated: bool,
    registered_at: str,
) -> bool:
    """Upsert an agent from an `on_agent_register` event.

    Returns True when the snapshot was persisted (new agent or hash
    changed), False when we reused the existing snapshot unchanged.
    """
    blob: bytes | None = None
    if code_snapshot_b64:
        try:
            blob = base64.b64decode(code_snapshot_b64)
        except ValueError:
            blob = None

    now = _utcnow_iso()
    async with get_db() as db:
        async with db.execute(
            "SELECT code_snapshot_hash FROM agents WHERE agent_id = ?",
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
        existing_hash = row[0] if row else None
        snapshot_changed = existing_hash != code_snapshot_hash

        if row is None:
            await db.execute(
                """
                INSERT INTO agents (
                    agent_id, name, framework, version, created_at,
                    last_seen_at, risk_score, metadata_json,
                    system_prompt, project_root, code_snapshot_blob,
                    code_snapshot_hash, file_count, total_bytes,
                    snapshot_truncated, registered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, '{}', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    agent_name,
                    framework,
                    version,
                    now,
                    now,
                    system_prompt,
                    project_root,
                    blob if snapshot_changed else None,
                    code_snapshot_hash,
                    file_count,
                    total_bytes,
                    1 if truncated else 0,
                    registered_at,
                ),
            )
        else:
            if snapshot_changed:
                await db.execute(
                    """
                    UPDATE agents SET
                        name = ?, framework = COALESCE(?, framework),
                        version = COALESCE(?, version),
                        system_prompt = COALESCE(?, system_prompt),
                        project_root = ?,
                        code_snapshot_blob = ?,
                        code_snapshot_hash = ?,
                        file_count = ?, total_bytes = ?,
                        snapshot_truncated = ?,
                        registered_at = ?, last_seen_at = ?
                    WHERE agent_id = ?
                    """,
                    (
                        agent_name,
                        framework,
                        version,
                        system_prompt,
                        project_root,
                        blob,
                        code_snapshot_hash,
                        file_count,
                        total_bytes,
                        1 if truncated else 0,
                        registered_at,
                        now,
                        agent_id,
                    ),
                )
            else:
                await db.execute(
                    """
                    UPDATE agents SET
                        name = ?, framework = COALESCE(?, framework),
                        version = COALESCE(?, version),
                        system_prompt = COALESCE(?, system_prompt),
                        last_seen_at = ?
                    WHERE agent_id = ?
                    """,
                    (
                        agent_name,
                        framework,
                        version,
                        system_prompt,
                        now,
                        agent_id,
                    ),
                )
        await db.commit()
    return snapshot_changed


async def update_agent_last_seen(agent_id: str) -> None:
    """Cheap heartbeat — every ingested event pings its agent's row."""
    async with get_db() as db:
        await db.execute(
            "UPDATE agents SET last_seen_at = ? WHERE agent_id = ?",
            (_utcnow_iso(), agent_id),
        )
        await db.commit()


async def update_agent_profile(
    agent_id: str,
    *,
    name: str | None = None,
    version: str | None = None,
    system_prompt: str | None = None,
) -> list[str]:
    """PATCH-style partial update. Returns the names of fields that changed."""
    sets: list[str] = []
    params: list[Any] = []
    changed: list[str] = []
    if name is not None:
        sets.append("name = ?")
        params.append(name)
        changed.append("name")
    if version is not None:
        sets.append("version = ?")
        params.append(version)
        changed.append("version")
    if system_prompt is not None:
        sets.append("system_prompt = ?")
        params.append(system_prompt)
        changed.append("system_prompt")
    if not sets:
        return []
    params.append(agent_id)
    async with get_db() as db:
        cur = await db.execute(
            f"UPDATE agents SET {', '.join(sets)} WHERE agent_id = ?",
            params,
        )
        await db.commit()
        if cur.rowcount == 0:
            return []
    return changed


async def set_agent_latest_scan(agent_id: str, scan_id: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE agents SET latest_scan_id = ? WHERE agent_id = ?",
            (scan_id, agent_id),
        )
        await db.commit()


async def insert_inspector_report(report: Any) -> None:
    """Persist an InspectorReport and bump the agent's latest_scan_id."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO inspector_reports (
                report_id, agent_id, created_at, scan_mode, risk_score,
                risk_level, duration_ms, persona_skipped, persona_error,
                report_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.report_id,
                report.agent_id,
                report.created_at.isoformat(),
                getattr(report, "scan_mode", "single"),
                report.risk_score,
                report.risk_level.value,
                report.duration_ms,
                1 if report.persona_review_skipped else 0,
                report.persona_review_error,
                report.model_dump_json(),
            ),
        )
        await db.execute(
            "UPDATE agents SET latest_scan_id = ? WHERE agent_id = ?",
            (report.report_id, report.agent_id),
        )
        await db.commit()


async def get_latest_inspector_report(agent_id: str) -> dict[str, Any] | None:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT report_json FROM inspector_reports
            WHERE agent_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def list_agent_summaries() -> list["AgentSummary"]:
    """Dashboard card grid feed."""
    from ..models.agent import AgentSummary

    async with get_db() as db:
        async with db.execute(
            """
            SELECT agent_id, name, framework, version, created_at,
                   last_seen_at, risk_score, latest_scan_id, file_count,
                   code_snapshot_hash
            FROM agents
            ORDER BY COALESCE(last_seen_at, created_at) DESC
            """
        ) as cur:
            rows = await cur.fetchall()
    out: list[AgentSummary] = []
    for row in rows:
        scan_status = "scanned" if row[7] else ("unscanned" if not row[9] else "unscanned")
        out.append(
            AgentSummary(
                agent_id=row[0],
                name=row[1],
                framework=row[2],
                version=row[3],
                created_at=_parse_iso(row[4]) or datetime.now(timezone.utc),
                last_seen_at=_parse_iso(row[5]),
                risk_score=row[6] or 0,
                latest_scan_id=row[7],
                scan_status=scan_status,  # type: ignore[arg-type]
                file_count=row[8] or 0,
            )
        )
    return out


async def get_agent_record(agent_id: str) -> "AgentRecord | None":
    """Full record minus the snapshot blob — the blob is fetched separately."""
    from ..models.agent import AgentRecord

    async with get_db() as db:
        async with db.execute(
            """
            SELECT agent_id, name, framework, version, system_prompt,
                   project_root, code_snapshot_hash, file_count, total_bytes,
                   snapshot_truncated, created_at, registered_at, last_seen_at,
                   latest_scan_id, risk_score
            FROM agents
            WHERE agent_id = ?
            """,
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return AgentRecord(
        agent_id=row[0],
        name=row[1],
        framework=row[2],
        version=row[3],
        system_prompt=row[4],
        project_root=row[5],
        code_snapshot_hash=row[6],
        file_count=row[7] or 0,
        total_bytes=row[8] or 0,
        snapshot_truncated=bool(row[9]),
        created_at=_parse_iso(row[10]) or datetime.now(timezone.utc),
        registered_at=_parse_iso(row[11]),
        last_seen_at=_parse_iso(row[12]),
        latest_scan_id=row[13],
        risk_score=row[14] or 0,
    )


async def get_agent_snapshot_blob(agent_id: str) -> bytes | None:
    """Pull the raw gzipped snapshot so the Inspector can scan it."""
    async with get_db() as db:
        async with db.execute(
            "SELECT code_snapshot_blob FROM agents WHERE agent_id = ?",
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    return bytes(row[0])


def unpack_snapshot_blob(blob: bytes) -> list[tuple[str, str]]:
    """Inverse of the SDK's `pack_snapshot` — returns `[(path, source), ...]`."""
    import gzip

    raw = gzip.decompress(blob)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("snapshot payload is not a dict")
    return [(str(k), str(v)) for k, v in data.items()]


async def list_agent_sessions(agent_id: str, limit: int = 100) -> list["AgentSessionRow"]:
    """Sessions & Reports tab feed."""
    from ..models.agent import AgentSessionRow

    async with get_db() as db:
        async with db.execute(
            """
            SELECT session_id, started_at, ended_at, total_steps,
                   total_cost_usd, success, overall_health, report_json
            FROM sessions
            WHERE agent_id = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    out: list[AgentSessionRow] = []
    for row in rows:
        out.append(
            AgentSessionRow(
                session_id=row[0],
                started_at=_parse_iso(row[1]) or datetime.now(timezone.utc),
                ended_at=_parse_iso(row[2]),
                total_steps=row[3] or 0,
                total_cost_usd=float(row[4] or 0.0),
                success=bool(row[5]),
                overall_health=row[6],
                has_report=bool(row[7]),
            )
        )
    return out


async def list_agent_redteam_runs(
    agent_id: str, limit: int = 50
) -> list["AgentRedTeamRow"]:
    """Red-Team Reports tab feed."""
    from ..models.agent import AgentRedTeamRow

    async with get_db() as db:
        async with db.execute(
            """
            SELECT run_id, mode, phase, started_at, finished_at,
                   findings_count, safety_score
            FROM red_team_runs
            WHERE agent_id = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    out: list[AgentRedTeamRow] = []
    for row in rows:
        out.append(
            AgentRedTeamRow(
                run_id=row[0],
                mode=row[1],
                phase=row[2],
                started_at=_parse_iso(row[3]) or datetime.now(timezone.utc),
                finished_at=_parse_iso(row[4]),
                findings_count=row[5] or 0,
                safety_score=row[6] or 0,
            )
        )
    return out


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
