"""Bootstrap Claude Managed Agents resources used by Red-Team Squad.

Creates (idempotently) three role-specialised agents and one shared
environment:

- `safer-redteam-strategist`  (Opus 4.7) — produces AttackSpec list.
- `safer-redteam-attacker`    (Opus 4.7) — simulates the target.
- `safer-redteam-analyst`     (Sonnet 4.6) — clusters into findings.
- `safer-redteam-env`         — cloud sandbox with unrestricted egress.

Resource IDs are persisted in the `managed_agents_config` SQLite
table (the same table the Inspector uses). First run creates them all
and records the IDs; subsequent runs read the cached IDs and return
them.

The orchestrator in `managed.py` is the only caller — tests inject a
fake client via `_set_beta_client_factory()` so this file's networking
can be exercised without an Anthropic key.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..storage.db import get_db
from ._client import (
    REDTEAM_ANALYST_MODEL,
    REDTEAM_ATTACKER_MODEL,
    REDTEAM_STRATEGIST_MODEL,
)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

log = logging.getLogger("safer.redteam.managed_bootstrap")

BETA_HEADER = "managed-agents-2026-04-01"
ENVIRONMENT_NAME = "safer-redteam-env"

STRATEGIST_AGENT_NAME = "SAFER Red-Team Strategist"
ATTACKER_AGENT_NAME = "SAFER Red-Team Attacker"
ANALYST_AGENT_NAME = "SAFER Red-Team Analyst"

_PROMPT_DIR = Path(__file__).parent
STRATEGIST_PROMPT_PATH = _PROMPT_DIR / "managed_strategist_prompt.md"
ATTACKER_PROMPT_PATH = _PROMPT_DIR / "managed_attacker_prompt.md"
ANALYST_PROMPT_PATH = _PROMPT_DIR / "managed_analyst_prompt.md"

_CONFIG_KEYS = {
    "strategist_agent_id": "redteam_strategist_agent_id",
    "attacker_agent_id": "redteam_attacker_agent_id",
    "analyst_agent_id": "redteam_analyst_agent_id",
    "env_id": "redteam_environment_id",
}


class ManagedBootstrapError(RuntimeError):
    """Raised when Red-Team Managed Agents resources cannot be provisioned."""


# ---------- DB helpers (mirrors inspector/managed_bootstrap.py) ----------


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


# ---------- Beta client (test-injectable) ----------


_beta_client_factory: Callable[[], "AsyncAnthropic"] | None = None


def _set_beta_client_factory(factory: Callable[[], Any] | None) -> None:
    """Test hook: inject a fake AsyncAnthropic factory.

    Pass None to restore the real factory.
    """
    global _beta_client_factory
    _beta_client_factory = factory


def _beta_client() -> "AsyncAnthropic":
    """Async Anthropic client with the Managed Agents beta header.

    Tests can override via `_set_beta_client_factory(lambda: fake)`.
    Raises ManagedBootstrapError if the API key or the SDK is missing.
    """
    if _beta_client_factory is not None:
        return _beta_client_factory()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ManagedBootstrapError("ANTHROPIC_API_KEY not set")
    try:
        from anthropic import AsyncAnthropic
    except ImportError as e:
        raise ManagedBootstrapError(f"anthropic SDK unavailable: {e}") from e
    return AsyncAnthropic(default_headers={"anthropic-beta": BETA_HEADER})


def _load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------- ensure_* helpers (one per agent + the env) ----------


async def _ensure_agent(
    *,
    config_key: str,
    name: str,
    model: str,
    prompt_path: Path,
    client: "AsyncAnthropic | None" = None,
) -> str:
    cfg = await _read_config()
    if cfg.get(config_key):
        return cfg[config_key]

    client = client or _beta_client()
    system_prompt = _load_prompt(prompt_path)

    try:
        agent = await client.beta.agents.create(
            name=name,
            model=model,
            system=system_prompt,
            tools=[{"type": "agent_toolset_20260401"}],
        )
    except Exception as e:
        raise ManagedBootstrapError(f"agents.create failed for {name}: {e}") from e

    agent_id = getattr(agent, "id", None) or (
        agent.get("id") if isinstance(agent, dict) else None
    )
    if not agent_id:
        raise ManagedBootstrapError(f"agents.create returned no id for {name}")

    await _write_config(config_key, agent_id)
    log.info(
        "created Red-Team Managed Agent name=%s id=%s model=%s",
        name,
        agent_id,
        model,
    )
    return agent_id


async def ensure_strategist_agent(
    client: "AsyncAnthropic | None" = None,
) -> str:
    """Return the Strategist agent id, creating it on first use."""
    return await _ensure_agent(
        config_key=_CONFIG_KEYS["strategist_agent_id"],
        name=STRATEGIST_AGENT_NAME,
        model=REDTEAM_STRATEGIST_MODEL,
        prompt_path=STRATEGIST_PROMPT_PATH,
        client=client,
    )


async def ensure_attacker_agent(
    client: "AsyncAnthropic | None" = None,
) -> str:
    """Return the Attacker agent id, creating it on first use."""
    return await _ensure_agent(
        config_key=_CONFIG_KEYS["attacker_agent_id"],
        name=ATTACKER_AGENT_NAME,
        model=REDTEAM_ATTACKER_MODEL,
        prompt_path=ATTACKER_PROMPT_PATH,
        client=client,
    )


async def ensure_analyst_agent(
    client: "AsyncAnthropic | None" = None,
) -> str:
    """Return the Analyst agent id, creating it on first use."""
    return await _ensure_agent(
        config_key=_CONFIG_KEYS["analyst_agent_id"],
        name=ANALYST_AGENT_NAME,
        model=REDTEAM_ANALYST_MODEL,
        prompt_path=ANALYST_PROMPT_PATH,
        client=client,
    )


async def ensure_environment(
    client: "AsyncAnthropic | None" = None,
) -> str:
    """Return the Red-Team environment id, creating it on first use."""
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

    env_id = getattr(environment, "id", None) or (
        environment.get("id") if isinstance(environment, dict) else None
    )
    if not env_id:
        raise ManagedBootstrapError("environments.create returned no id")

    await _write_config(_CONFIG_KEYS["env_id"], env_id)
    log.info("created Red-Team Managed environment id=%s", env_id)
    return env_id


async def ensure_all() -> dict[str, str]:
    """Provision every Red-Team Managed resource and return their ids.

    Order: Strategist → Attacker → Analyst → environment. Idempotent.
    """
    client = _beta_client()
    strategist = await ensure_strategist_agent(client)
    attacker = await ensure_attacker_agent(client)
    analyst = await ensure_analyst_agent(client)
    env_id = await ensure_environment(client)
    return {
        "strategist_agent_id": strategist,
        "attacker_agent_id": attacker,
        "analyst_agent_id": analyst,
        "env_id": env_id,
    }
