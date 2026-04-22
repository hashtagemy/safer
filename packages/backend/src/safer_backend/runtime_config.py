"""Runtime-mutable configuration.

The env vars we read at startup become the *initial* value of each
field here. The dashboard is allowed to change them at runtime via
`PATCH /v1/config`. Everything still lives in memory — restarts re-
read the env.

Separate from `SaferConfig` on the SDK side, which is client-only.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass

log = logging.getLogger("safer.runtime_config")


VALID_GUARD_MODES: tuple[str, ...] = ("monitor", "intervene", "enforce")
VALID_JUDGE_MODES: tuple[str, ...] = ("auto", "on", "off")
VALID_REDTEAM_MODES: tuple[str, ...] = ("managed", "subagent")


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _seed_guard_mode() -> str:
    raw = (os.environ.get("SAFER_GUARD_MODE") or "monitor").lower()
    return raw if raw in VALID_GUARD_MODES else "monitor"


def _seed_judge_mode() -> str:
    raw = (os.environ.get("SAFER_JUDGE_ENABLED") or "auto").lower()
    return raw if raw in VALID_JUDGE_MODES else "auto"


def _seed_judge_max_tokens() -> int:
    raw = os.environ.get("SAFER_JUDGE_MAX_TOKENS") or "2000"
    try:
        return _clamp(int(raw), 256, 8000)
    except ValueError:
        return 2000


def _seed_redteam_mode() -> str:
    raw = (os.environ.get("RED_TEAM_MODE") or "subagent").lower()
    return raw if raw in VALID_REDTEAM_MODES else "subagent"


def _seed_redteam_num_attacks() -> int:
    raw = os.environ.get("SAFER_REDTEAM_DEFAULT_ATTACKS") or "10"
    try:
        return _clamp(int(raw), 1, 30)
    except ValueError:
        return 10


def _seed_retention_days() -> int:
    raw = os.environ.get("SAFER_RETENTION_DAYS") or "90"
    try:
        return _clamp(int(raw), 1, 3650)
    except ValueError:
        return 90


@dataclass
class RuntimeConfig:
    guard_mode: str
    judge_enabled: str
    judge_max_tokens: int
    redteam_default_mode: str
    redteam_default_num_attacks: int
    retention_days: int


_config = RuntimeConfig(
    guard_mode=_seed_guard_mode(),
    judge_enabled=_seed_judge_mode(),
    judge_max_tokens=_seed_judge_max_tokens(),
    redteam_default_mode=_seed_redteam_mode(),
    redteam_default_num_attacks=_seed_redteam_num_attacks(),
    retention_days=_seed_retention_days(),
)
_lock = asyncio.Lock()


# ---------- getters ----------


def get_guard_mode() -> str:
    return _config.guard_mode


def get_judge_enabled() -> str:
    return _config.judge_enabled


def get_judge_max_tokens() -> int:
    return _config.judge_max_tokens


def get_redteam_default_mode() -> str:
    return _config.redteam_default_mode


def get_redteam_default_num_attacks() -> int:
    return _config.redteam_default_num_attacks


def get_retention_days() -> int:
    return _config.retention_days


# ---------- setters ----------


async def set_guard_mode(mode: str) -> str:
    mode = (mode or "").lower()
    if mode not in VALID_GUARD_MODES:
        raise ValueError(
            f"guard_mode must be one of {VALID_GUARD_MODES}, got {mode!r}"
        )
    async with _lock:
        _config.guard_mode = mode
    log.info("guard_mode → %s", mode)
    return mode


async def set_judge_enabled(mode: str) -> str:
    mode = (mode or "").lower()
    if mode not in VALID_JUDGE_MODES:
        raise ValueError(
            f"judge_enabled must be one of {VALID_JUDGE_MODES}, got {mode!r}"
        )
    async with _lock:
        _config.judge_enabled = mode
    log.info("judge_enabled → %s", mode)
    return mode


async def set_judge_max_tokens(n: int) -> int:
    n = _clamp(int(n), 256, 8000)
    async with _lock:
        _config.judge_max_tokens = n
    log.info("judge_max_tokens → %d", n)
    return n


async def set_redteam_default_mode(mode: str) -> str:
    mode = (mode or "").lower()
    if mode not in VALID_REDTEAM_MODES:
        raise ValueError(
            f"redteam_default_mode must be one of {VALID_REDTEAM_MODES}, got {mode!r}"
        )
    async with _lock:
        _config.redteam_default_mode = mode
    log.info("redteam_default_mode → %s", mode)
    return mode


async def set_redteam_default_num_attacks(n: int) -> int:
    n = _clamp(int(n), 1, 30)
    async with _lock:
        _config.redteam_default_num_attacks = n
    log.info("redteam_default_num_attacks → %d", n)
    return n


async def set_retention_days(n: int) -> int:
    n = _clamp(int(n), 1, 3650)
    async with _lock:
        _config.retention_days = n
    log.info("retention_days → %d", n)
    return n


# ---------- snapshot ----------


def snapshot() -> dict[str, object]:
    return {
        **asdict(_config),
        "valid_guard_modes": list(VALID_GUARD_MODES),
        "valid_judge_modes": list(VALID_JUDGE_MODES),
        "valid_redteam_modes": list(VALID_REDTEAM_MODES),
    }
