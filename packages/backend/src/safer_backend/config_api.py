"""Runtime config endpoints — GET + PATCH.

The dashboard's /settings page uses these to change Gateway guard mode
without a restart. All mutations are in-memory; the container restart
re-reads the `SAFER_GUARD_MODE` env var.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import runtime_config

router = APIRouter(prefix="/v1/config", tags=["config"])


class ConfigSnapshot(BaseModel):
    guard_mode: str
    valid_guard_modes: list[str]


class PatchRequest(BaseModel):
    guard_mode: Literal["monitor", "intervene", "enforce"] | None = None


@router.get("", response_model=ConfigSnapshot)
async def get_config() -> ConfigSnapshot:
    return ConfigSnapshot(**runtime_config.snapshot())  # type: ignore[arg-type]


@router.patch("", response_model=ConfigSnapshot)
async def patch_config(request: PatchRequest) -> ConfigSnapshot:
    if request.guard_mode is not None:
        try:
            await runtime_config.set_guard_mode(request.guard_mode)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    return ConfigSnapshot(**runtime_config.snapshot())  # type: ignore[arg-type]
