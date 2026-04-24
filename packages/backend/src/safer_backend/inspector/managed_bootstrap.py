"""Bootstrap Claude Managed Agents resources used by the Inspector.

Creates (idempotently):
- an `agent` with the three-persona Inspector system prompt and the
  full agent toolset,
- a `memory_store` named `safer-inspector-knowledge`, shared across
  every Inspector session (persists learned patterns),
- an `environment` with the default cloud/unrestricted-networking
  configuration.

Resource IDs are persisted in the `managed_agents_config` SQLite
table. First run creates all three and records the IDs; subsequent
runs read the cached IDs and return them.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..storage.db import get_db

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

log = logging.getLogger("safer.inspector.managed_bootstrap")

BETA_HEADER = "managed-agents-2026-04-01"
INSPECTOR_MODEL = "claude-opus-4-7"
MEMORY_STORE_NAME = "safer-inspector-knowledge"
MEMORY_STORE_DESCRIPTION = (
    "Patterns learned by SAFER Inspector across every agent it has "
    "reviewed. One file per pattern, grouped by category. Read this "
    "BEFORE analyzing the target; append genuinely new patterns AFTER."
)
ENVIRONMENT_NAME = "safer-inspector-env"

SYSTEM_PROMPT_PATH = Path(__file__).parent / "managed_system_prompt.md"

_CONFIG_KEYS = {
    "agent_id": "inspector_agent_id",
    "store_id": "inspector_memory_store_id",
    "env_id": "inspector_environment_id",
}


class ManagedBootstrapError(RuntimeError):
    """Raised when Managed Agents resources cannot be provisioned."""


async def _read_config() -> dict[str, str]:
    async with get_db() as db:
        async with db.execute(
            "SELECT key, value FROM managed_agents_config"
        ) as cur:
            rows = await cur.fetchall()
    return {row[0]: row[1] for row in rows}


async def _write_config(key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO managed_agents_config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        await db.commit()


def _load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _beta_client() -> "AsyncAnthropic":
    """Async Anthropic client with the Managed Agents beta header set.

    Raises ManagedBootstrapError if the API key is missing or the
    anthropic SDK is not importable.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ManagedBootstrapError("ANTHROPIC_API_KEY not set")
    try:
        from anthropic import AsyncAnthropic
    except ImportError as e:
        raise ManagedBootstrapError(f"anthropic SDK unavailable: {e}") from e
    return AsyncAnthropic(default_headers={"anthropic-beta": BETA_HEADER})


async def ensure_inspector_agent(
    client: "AsyncAnthropic | None" = None,
) -> str:
    """Return the Inspector agent ID, creating it on first use."""
    cfg = await _read_config()
    if cfg.get(_CONFIG_KEYS["agent_id"]):
        return cfg[_CONFIG_KEYS["agent_id"]]

    client = client or _beta_client()
    system_prompt = _load_system_prompt()

    try:
        agent = await client.beta.agents.create(
            name="SAFER Inspector",
            model=INSPECTOR_MODEL,
            system=system_prompt,
            tools=[{"type": "agent_toolset_20260401"}],
        )
    except Exception as e:
        raise ManagedBootstrapError(f"agents.create failed: {e}") from e

    await _write_config(_CONFIG_KEYS["agent_id"], agent.id)
    log.info("created Managed Agent id=%s version=%s", agent.id, agent.version)
    return agent.id


async def ensure_memory_store(
    client: "AsyncAnthropic | None" = None,
) -> str:
    """Return the shared Inspector memory store ID, creating on first use."""
    cfg = await _read_config()
    if cfg.get(_CONFIG_KEYS["store_id"]):
        return cfg[_CONFIG_KEYS["store_id"]]

    client = client or _beta_client()
    try:
        store = await client.beta.memory_stores.create(
            name=MEMORY_STORE_NAME,
            description=MEMORY_STORE_DESCRIPTION,
        )
    except Exception as e:
        raise ManagedBootstrapError(f"memory_stores.create failed: {e}") from e

    await _write_config(_CONFIG_KEYS["store_id"], store.id)
    log.info("created memory store id=%s", store.id)
    return store.id


async def ensure_environment(
    client: "AsyncAnthropic | None" = None,
) -> str:
    """Return the Inspector environment ID, creating it on first use."""
    cfg = await _read_config()
    if cfg.get(_CONFIG_KEYS["env_id"]):
        return cfg[_CONFIG_KEYS["env_id"]]

    client = client or _beta_client()
    try:
        environment = await client.beta.environments.create(
            name=ENVIRONMENT_NAME,
            config={
                "type": "cloud",
                "networking": {"type": "unrestricted"},
            },
        )
    except Exception as e:
        raise ManagedBootstrapError(
            f"environments.create failed: {e}"
        ) from e

    await _write_config(_CONFIG_KEYS["env_id"], environment.id)
    log.info("created environment id=%s", environment.id)
    return environment.id


async def ensure_all() -> dict[str, str]:
    """Return {agent_id, store_id, env_id}, provisioning any that are missing."""
    client = _beta_client()
    agent_id = await ensure_inspector_agent(client)
    store_id = await ensure_memory_store(client)
    env_id = await ensure_environment(client)
    return {"agent_id": agent_id, "store_id": store_id, "env_id": env_id}
