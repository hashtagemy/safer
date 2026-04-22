"""Admin-grade actions: purge old rows, VACUUM the DB.

Kept behind the normal CORS layer since SAFER is self-hosted; deploy
it behind your own auth layer if you expose the port externally.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .storage.db import DEFAULT_DB_PATH, get_db

log = logging.getLogger("safer.admin_api")

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class PurgeResult(BaseModel):
    deleted_events: int
    deleted_verdicts: int
    deleted_findings: int
    deleted_claude_calls: int
    older_than: str


class VacuumResult(BaseModel):
    bytes_before: int
    bytes_after: int
    bytes_reclaimed: int


def _db_size() -> int:
    try:
        return os.path.getsize(DEFAULT_DB_PATH)
    except OSError:
        return 0


@router.delete("/events", response_model=PurgeResult)
async def purge_old_events(
    older_than_days: int = Query(default=90, ge=1, le=3650),
) -> PurgeResult:
    """Delete events + their dependents strictly older than N days.

    Runs in a single transaction. Session rows are kept so the
    dashboard's sessions list stays meaningful; only the append-only
    signal data (events, verdicts, findings, claude_calls) is purged.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    async with get_db() as db:
        # Use separate cursors so each returns its rowcount.
        cur = await db.execute(
            "DELETE FROM verdicts WHERE timestamp < ?", (cutoff,)
        )
        deleted_verdicts = cur.rowcount or 0
        cur = await db.execute(
            "DELETE FROM findings WHERE created_at < ?", (cutoff,)
        )
        deleted_findings = cur.rowcount or 0
        cur = await db.execute(
            "DELETE FROM claude_calls WHERE timestamp < ?", (cutoff,)
        )
        deleted_calls = cur.rowcount or 0
        cur = await db.execute(
            "DELETE FROM events WHERE timestamp < ?", (cutoff,)
        )
        deleted_events = cur.rowcount or 0
        await db.commit()

    log.info(
        "purge: events=%d verdicts=%d findings=%d calls=%d older_than=%s",
        deleted_events,
        deleted_verdicts,
        deleted_findings,
        deleted_calls,
        cutoff,
    )
    return PurgeResult(
        deleted_events=deleted_events,
        deleted_verdicts=deleted_verdicts,
        deleted_findings=deleted_findings,
        deleted_claude_calls=deleted_calls,
        older_than=cutoff,
    )


@router.post("/vacuum", response_model=VacuumResult)
async def vacuum_db() -> VacuumResult:
    before = _db_size()
    async with get_db() as db:
        await db.execute("VACUUM")
        await db.commit()
    after = _db_size()
    log.info("vacuum: %d → %d bytes", before, after)
    return VacuumResult(
        bytes_before=before,
        bytes_after=after,
        bytes_reclaimed=max(0, before - after),
    )
