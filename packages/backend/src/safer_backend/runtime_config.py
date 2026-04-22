"""Runtime-mutable configuration (guard mode today; agent-scoped rules next).

The env vars we read at startup (`SAFER_GUARD_MODE`, …) become the
*initial* value of each field here. The dashboard is allowed to change
them at runtime via `PATCH /v1/config`. Everything still lives in
memory — restarts re-read the env.

Separate from `SaferConfig` on the SDK side, which is client-only.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

log = logging.getLogger("safer.runtime_config")


VALID_GUARD_MODES: tuple[str, ...] = ("monitor", "intervene", "enforce")


def _default_guard_mode() -> str:
    raw = (os.environ.get("SAFER_GUARD_MODE") or "monitor").lower()
    return raw if raw in VALID_GUARD_MODES else "monitor"


@dataclass
class RuntimeConfig:
    guard_mode: str = "monitor"


_config = RuntimeConfig(guard_mode=_default_guard_mode())
_lock = asyncio.Lock()


def get_guard_mode() -> str:
    return _config.guard_mode


async def set_guard_mode(mode: str) -> str:
    mode = (mode or "").lower()
    if mode not in VALID_GUARD_MODES:
        raise ValueError(
            f"guard_mode must be one of {VALID_GUARD_MODES}, got {mode!r}"
        )
    async with _lock:
        _config.guard_mode = mode
    log.info("guard_mode updated to %s", mode)
    return mode


def snapshot() -> dict[str, object]:
    return {
        "guard_mode": _config.guard_mode,
        "valid_guard_modes": list(VALID_GUARD_MODES),
    }
