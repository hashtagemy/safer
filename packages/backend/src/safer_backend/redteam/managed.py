"""Red-Team Managed Agents mode — stretch goal stub.

The Anthropic Managed Agents API is the eventual home of this flow
(three specialised agents — Strategist, Attacker, Analyst — running
under Anthropic control). In the hackathon MVP we call `attempt_managed`
and let it return `None` on any failure so the orchestrator transparently
falls back to sub-agent mode.

When the Managed Agents API becomes available in this environment, this
module fills out to call it; the rest of the orchestrator is unchanged.
"""

from __future__ import annotations

import logging

from ..models.redteam import RedTeamRun

log = logging.getLogger("safer.redteam.managed")


async def attempt_managed(
    *,
    agent_id: str,
    target_system_prompt: str,
    target_tools: list[str],
    num_attacks: int,
    run: RedTeamRun,
) -> RedTeamRun | None:
    """Try to run via Managed Agents. Return None to signal fallback.

    In this MVP we unconditionally signal fallback. Leaving the function
    in place keeps the orchestrator's code path stable — the switch to
    a real Managed implementation becomes a body-change, not a control-
    flow rewrite.
    """
    log.info(
        "managed red-team requested for agent=%s run=%s; stub returning None",
        agent_id,
        run.run_id,
    )
    return None
