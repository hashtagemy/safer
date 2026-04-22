"""Runtime config endpoints.

All fields are in-memory; the container restart re-reads env vars.
The dashboard's /settings page is the primary consumer.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import runtime_config

router = APIRouter(prefix="/v1/config", tags=["config"])


class ConfigSnapshot(BaseModel):
    guard_mode: str
    judge_enabled: str
    judge_max_tokens: int
    redteam_default_mode: str
    redteam_default_num_attacks: int
    retention_days: int
    valid_guard_modes: list[str]
    valid_judge_modes: list[str]
    valid_redteam_modes: list[str]


class PatchRequest(BaseModel):
    guard_mode: Literal["monitor", "intervene", "enforce"] | None = None
    judge_enabled: Literal["auto", "on", "off"] | None = None
    judge_max_tokens: int | None = Field(default=None, ge=256, le=8000)
    redteam_default_mode: Literal["managed", "subagent"] | None = None
    redteam_default_num_attacks: int | None = Field(default=None, ge=1, le=30)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


@router.get("", response_model=ConfigSnapshot)
async def get_config() -> ConfigSnapshot:
    return ConfigSnapshot(**runtime_config.snapshot())  # type: ignore[arg-type]


@router.patch("", response_model=ConfigSnapshot)
async def patch_config(request: PatchRequest) -> ConfigSnapshot:
    try:
        if request.guard_mode is not None:
            await runtime_config.set_guard_mode(request.guard_mode)
        if request.judge_enabled is not None:
            await runtime_config.set_judge_enabled(request.judge_enabled)
        if request.judge_max_tokens is not None:
            await runtime_config.set_judge_max_tokens(request.judge_max_tokens)
        if request.redteam_default_mode is not None:
            await runtime_config.set_redteam_default_mode(request.redteam_default_mode)
        if request.redteam_default_num_attacks is not None:
            await runtime_config.set_redteam_default_num_attacks(
                request.redteam_default_num_attacks
            )
        if request.retention_days is not None:
            await runtime_config.set_retention_days(request.retention_days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ConfigSnapshot(**runtime_config.snapshot())  # type: ignore[arg-type]
