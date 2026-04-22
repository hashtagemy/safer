"""Read-only system information endpoint.

The dashboard's /settings page uses this to render the "System" card
(version, models, uptime, DB footprint, Claude cost ratios).
"""

from __future__ import annotations

import os
import platform
import sys
import time

from fastapi import APIRouter
from pydantic import BaseModel

from .storage.db import DEFAULT_DB_PATH, get_db

router = APIRouter(prefix="/v1/system", tags=["system"])


_START_TS = time.time()


class SystemInfo(BaseModel):
    safer_version: str
    python_version: str
    platform: str
    uptime_seconds: int

    db_path: str
    db_size_bytes: int

    judge_model: str
    haiku_model: str
    policy_compiler_model: str
    redteam_model: str

    total_opus_calls: int
    total_haiku_calls: int
    total_tokens_in: int
    total_tokens_out: int
    total_cache_read_tokens: int
    cache_read_ratio: float


def _db_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


@router.get("", response_model=SystemInfo)
async def get_system_info() -> SystemInfo:
    # Model choices mirror CLAUDE.md: Opus 4.7 for reasoning, Haiku 4.5
    # for the decision-hook per-step score.
    opus_model = os.environ.get("SAFER_JUDGE_MODEL", "claude-opus-4-7")

    async with get_db() as db:
        async with db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN model LIKE 'claude-opus%' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN model LIKE 'claude-haiku%' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(tokens_in), 0),
                COALESCE(SUM(tokens_out), 0),
                COALESCE(SUM(cache_read_tokens), 0)
            FROM claude_calls
            """
        ) as cur:
            row = await cur.fetchone()

    opus_calls = int(row[0] or 0)
    haiku_calls = int(row[1] or 0)
    tokens_in = int(row[2] or 0)
    tokens_out = int(row[3] or 0)
    cache_read = int(row[4] or 0)
    cache_read_ratio = cache_read / tokens_in if tokens_in > 0 else 0.0

    return SystemInfo(
        safer_version="0.1.0",
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        platform=f"{platform.system()} {platform.machine()}",
        uptime_seconds=int(time.time() - _START_TS),
        db_path=DEFAULT_DB_PATH,
        db_size_bytes=_db_size(DEFAULT_DB_PATH),
        judge_model=opus_model,
        haiku_model="claude-haiku-4-5",
        policy_compiler_model=os.environ.get("SAFER_POLICY_MODEL", opus_model),
        redteam_model=os.environ.get("SAFER_REDTEAM_MODEL", opus_model),
        total_opus_calls=opus_calls,
        total_haiku_calls=haiku_calls,
        total_tokens_in=tokens_in,
        total_tokens_out=tokens_out,
        total_cache_read_tokens=cache_read,
        cache_read_ratio=round(cache_read_ratio, 4),
    )
