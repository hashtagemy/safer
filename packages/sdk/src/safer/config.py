"""SDK configuration — env + defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

GuardMode = Literal["monitor", "intervene", "enforce"]


@dataclass(frozen=True)
class SaferConfig:
    api_url: str = "http://localhost:8000"
    api_key: str | None = None  # optional bearer for backend auth
    guard_mode: GuardMode = "monitor"
    batch_size: int = 50
    batch_interval_ms: int = 500
    max_buffer: int = 10_000  # drop-with-warning threshold
    shutdown_timeout_s: float = 2.0
    agent_id: str | None = None  # auto-generated if None
    agent_name: str | None = None

    @classmethod
    def from_env(cls, **overrides) -> "SaferConfig":
        def _env(name: str, default: str | None = None) -> str | None:
            v = os.environ.get(name)
            return v if v is not None and v != "" else default

        guard_mode_raw = (_env("SAFER_GUARD_MODE") or "monitor").lower()
        guard_mode: GuardMode = (
            guard_mode_raw if guard_mode_raw in ("monitor", "intervene", "enforce") else "monitor"  # type: ignore[assignment]
        )
        cfg = cls(
            api_url=_env("SAFER_API_URL", "http://localhost:8000"),  # type: ignore[arg-type]
            api_key=_env("SAFER_API_KEY"),
            guard_mode=guard_mode,
            batch_size=int(_env("SAFER_BATCH_SIZE", "50") or 50),
            batch_interval_ms=int(_env("SAFER_BATCH_INTERVAL_MS", "500") or 500),
            max_buffer=int(_env("SAFER_MAX_BUFFER", "10000") or 10000),
            shutdown_timeout_s=float(_env("SAFER_SHUTDOWN_TIMEOUT_S", "2.0") or 2.0),
            agent_id=_env("SAFER_AGENT_ID"),
            agent_name=_env("SAFER_AGENT_NAME"),
        )
        if overrides:
            # dataclass is frozen → replace
            from dataclasses import replace

            cfg = replace(cfg, **overrides)
        return cfg
