"""Red-Team run orchestrator — Strategist → Attacker → Analyst.

Persists a `RedTeamRun` row in the `red_team_runs` table as it moves
through phases (`planning` → `attacking` → `analyzing` → `done`),
broadcasts `redteam_phase` messages over the dashboard websocket, and
writes generated Findings into the `findings` table.

Two modes:

- `subagent` (default) — plain Opus calls for all three stages.
- `managed`  — stretch goal; see `managed.py`. On any failure this
  module silently falls back to `subagent`.

Red-Team is ALWAYS manual (no continuous mode). Kicked off by the
HTTP endpoint in `api.py`; never by the event router.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from ..models.findings import Finding
from ..models.redteam import (
    AttackSpec,
    Attempt,
    RedTeamMode,
    RedTeamPhase,
    RedTeamRun,
)
from ..storage.db import get_db
from ..ws_broadcaster import broadcaster
from .analyst import analyze_attempts
from .attacker import run_attacks
from .managed import attempt_managed
from .strategist import plan_attacks

log = logging.getLogger("safer.redteam.orchestrator")


def _env_mode() -> RedTeamMode:
    raw = os.environ.get("RED_TEAM_MODE", "subagent").lower()
    return RedTeamMode.MANAGED if raw == "managed" else RedTeamMode.SUBAGENT


async def _persist_run(run: RedTeamRun) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO red_team_runs
            (run_id, agent_id, mode, phase, started_at, finished_at,
             attack_specs_json, attempts_json, findings_count, safety_score,
             owasp_map_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                phase = excluded.phase,
                finished_at = excluded.finished_at,
                attack_specs_json = excluded.attack_specs_json,
                attempts_json = excluded.attempts_json,
                findings_count = excluded.findings_count,
                safety_score = excluded.safety_score,
                owasp_map_json = excluded.owasp_map_json,
                error = excluded.error
            """,
            (
                run.run_id,
                run.agent_id,
                run.mode.value,
                run.phase.value,
                run.started_at.isoformat(),
                run.finished_at.isoformat() if run.finished_at else None,
                json.dumps([a.model_dump(mode="json") for a in run.attack_specs]),
                json.dumps([a.model_dump(mode="json") for a in run.attempts]),
                run.findings_count,
                run.safety_score,
                json.dumps(run.owasp_map),
                run.error,
            ),
        )
        await db.commit()


async def _persist_findings(findings: list[Finding]) -> None:
    if not findings:
        return
    async with get_db() as db:
        for f in findings:
            await db.execute(
                """
                INSERT INTO findings
                (finding_id, agent_id, session_id, source, severity, category,
                 flag, title, description, evidence_json, reproduction_steps_json,
                 recommended_mitigation, owasp_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f.finding_id,
                    f.agent_id,
                    f.session_id,
                    f.source.value,
                    f.severity.value,
                    f.category,
                    f.flag,
                    f.title,
                    f.description,
                    json.dumps(list(f.evidence)),
                    json.dumps(list(f.reproduction_steps)),
                    f.recommended_mitigation,
                    f.owasp_id,
                    f.created_at.isoformat(),
                ),
            )
        await db.commit()


async def _broadcast_phase(run: RedTeamRun, *, extra: dict[str, Any] | None = None) -> None:
    msg: dict[str, Any] = {
        "type": "redteam_phase",
        "run_id": run.run_id,
        "agent_id": run.agent_id,
        "phase": run.phase.value,
        "mode": run.mode.value,
    }
    if extra:
        msg.update(extra)
    await broadcaster.broadcast(msg)


async def run_redteam(
    *,
    agent_id: str,
    target_system_prompt: str,
    target_tools: list[str] | None = None,
    target_name: str = "",
    num_attacks: int = 10,
    mode: RedTeamMode | None = None,
) -> RedTeamRun:
    """Run a Red-Team pass end-to-end and return the final `RedTeamRun`.

    Never raises on Claude errors; on failure the run is marked FAILED
    and the error is persisted.
    """
    chosen_mode = mode or _env_mode()
    run = RedTeamRun(agent_id=agent_id, mode=chosen_mode, phase=RedTeamPhase.PLANNING)
    await _persist_run(run)
    await _broadcast_phase(run)

    # --- Try Managed mode first (if requested). Fall back on any failure. ---
    if chosen_mode == RedTeamMode.MANAGED:
        try:
            managed_result = await attempt_managed(
                agent_id=agent_id,
                target_system_prompt=target_system_prompt,
                target_tools=target_tools or [],
                num_attacks=num_attacks,
                run=run,
            )
            if managed_result is not None:
                return managed_result
            log.info("managed mode unavailable; falling back to subagent")
        except Exception as e:
            log.warning("managed red-team failed, falling back: %s", e)
        run.mode = RedTeamMode.SUBAGENT
        await _persist_run(run)

    # --- Subagent path ---
    try:
        specs = await plan_attacks(
            target_system_prompt=target_system_prompt,
            target_tools=target_tools or [],
            target_name=target_name,
            num_attacks=num_attacks,
            agent_id=agent_id,
            run_id=run.run_id,
        )
        run.attack_specs = specs
        run.phase = RedTeamPhase.ATTACKING
        await _persist_run(run)
        await _broadcast_phase(run, extra={"attack_count": len(specs)})

        attempts: list[Attempt] = await run_attacks(
            attacks=specs,
            target_system_prompt=target_system_prompt,
            target_tools=target_tools or [],
            run_id=run.run_id,
            agent_id=agent_id,
        )
        run.attempts = attempts
        run.phase = RedTeamPhase.ANALYZING
        await _persist_run(run)
        await _broadcast_phase(run, extra={"attempt_count": len(attempts)})

        findings, owasp_map, safety_score = await analyze_attempts(
            attempts=attempts,
            attack_specs=specs,
            agent_id=agent_id,
            run_id=run.run_id,
        )
        run.findings_count = len(findings)
        run.owasp_map = owasp_map
        run.safety_score = safety_score
        run.phase = RedTeamPhase.DONE
        run.finished_at = datetime.now(timezone.utc)
        await _persist_run(run)
        await _persist_findings(findings)
        await _broadcast_phase(
            run,
            extra={
                "findings_count": len(findings),
                "safety_score": safety_score,
                "owasp_map": owasp_map,
            },
        )
        return run
    except Exception as e:
        log.exception("red-team run failed: %s", e)
        run.phase = RedTeamPhase.FAILED
        run.error = f"{type(e).__name__}: {e}"
        run.finished_at = datetime.now(timezone.utc)
        await _persist_run(run)
        await _broadcast_phase(run, extra={"error": run.error})
        return run
