"""Session Report orchestrator.

Coordinates the three layers:
1. QualityReviewer (Opus, one call) — only if a Claude client is
   available. Otherwise the quality category defaults to 100 (neutral).
2. Reconstructor (Opus, one call) — only when the session has at least
   one HIGH/CRITICAL verdict, OR when the caller forces it.
3. Aggregator (pure Python) — folds everything into a `SessionReport`.

The result is persisted to `sessions.report_json` +
`sessions.overall_health` + `sessions.thought_chain_narrative` so a
subsequent GET returns it immediately without recomputing.
"""

from __future__ import annotations

import json
import logging

from ..models.session_report import SessionReport
from ..storage.db import get_db
from .aggregator import aggregate

log = logging.getLogger("safer.session_report.orchestrator")


async def _has_elevated_verdict(session_id: str) -> bool:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT COUNT(*)
            FROM verdicts
            WHERE session_id = ? AND overall_risk IN ('HIGH', 'CRITICAL')
            """,
            (session_id,),
        ) as cur:
            (count,) = await cur.fetchone()
    return count > 0


async def generate_report(
    session_id: str,
    *,
    force_reconstruct: bool = False,
) -> SessionReport:
    """Produce a `SessionReport` for the session and persist it.

    Runs the two Opus layers when they are available; falls back to
    deterministic-only output otherwise.
    """
    quality = None
    try:
        from ..quality.reviewer import review_session

        quality = await review_session(session_id)
    except RuntimeError as e:
        # No Claude client — carry on with a neutral quality score.
        log.info("quality review skipped: %s", e)
    except Exception as e:  # pragma: no cover — defensive
        log.exception("quality review failed: %s", e)

    chain = None
    if force_reconstruct or await _has_elevated_verdict(session_id):
        try:
            from ..reconstructor.chain import reconstruct

            chain = await reconstruct(session_id)
        except RuntimeError as e:
            log.info("reconstruct skipped: %s", e)
        except Exception as e:  # pragma: no cover
            log.exception("reconstruct failed: %s", e)

    report = await aggregate(session_id, quality=quality, chain=chain)
    await _persist_report(report)
    return report


async def _persist_report(report: SessionReport) -> None:
    async with get_db() as db:
        await db.execute(
            """
            UPDATE sessions
            SET overall_health = ?,
                thought_chain_narrative = ?,
                report_json = ?,
                total_cost_usd = ?,
                total_steps = ?,
                success = ?
            WHERE session_id = ?
            """,
            (
                report.overall_health,
                report.thought_chain_narrative,
                report.model_dump_json(),
                report.cost.total_usd,
                report.total_steps,
                1 if report.success else 0,
                report.session_id,
            ),
        )
        await db.commit()


async def load_cached_report(session_id: str) -> SessionReport | None:
    """Return the persisted report for this session, or None if not yet generated."""
    async with get_db() as db:
        async with db.execute(
            "SELECT report_json FROM sessions WHERE session_id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        raise ValueError(f"session {session_id} not found")
    raw = row[0]
    if not raw:
        return None
    try:
        return SessionReport.model_validate(json.loads(raw))
    except Exception:
        return None
