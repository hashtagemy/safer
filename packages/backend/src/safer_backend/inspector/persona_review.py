"""3-persona Judge review in INSPECTOR mode.

Wraps `judge_event` with a synthetic event that describes the agent's
static properties (source code, system prompt, tool set, AST summary,
deterministic pattern matches, active user policies). The Judge returns
a `Verdict` limited to the three personas relevant at onboarding time:

- Security Auditor  — code-level attack surface
- Compliance Officer — data-handling posture
- Policy Warden     — conflicts with declared user policies

Trust Guardian / Scope Enforcer / Ethics Reviewer are *not* included;
they are behavioral and their persona prompts explicitly return "N/A"
in INSPECTOR mode, so excluding them saves Opus output tokens.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..judge.engine import JudgeMode, judge_event
from ..models.inspector import ASTSummary, PatternMatch, ToolSpec
from ..models.verdicts import Verdict

INSPECTOR_PERSONAS: list[str] = [
    "security_auditor",
    "compliance_officer",
    "policy_warden",
]


def build_synthetic_event(
    *,
    agent_id: str,
    source: str,
    system_prompt: str,
    tools: list[ToolSpec],
    ast_summary: ASTSummary,
    pattern_matches: list[PatternMatch],
    event_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the INSPECTOR-mode user message payload.

    This dict is serialized as the Judge's user message; the personas'
    INSPECTOR sections interpret it.
    """
    return {
        "event_id": event_id or f"ins_{uuid4().hex[:12]}",
        "agent_id": agent_id,
        "session_id": "",
        "hook": "inspector_review",
        "sequence": 0,
        "inspector": True,
        "code": source,
        "system_prompt": system_prompt,
        "tools": [t.model_dump(mode="json") for t in tools],
        "ast_summary": ast_summary.model_dump(mode="json"),
        "pattern_matches": [m.model_dump(mode="json") for m in pattern_matches],
    }


async def review(
    *,
    agent_id: str,
    source: str,
    system_prompt: str = "",
    tools: list[ToolSpec] | None = None,
    ast_summary: ASTSummary,
    pattern_matches: list[PatternMatch] | None = None,
    active_policies: list[dict[str, Any]] | None = None,
) -> Verdict:
    """Run the 3-persona Judge in INSPECTOR mode.

    Returns a `Verdict`. Callers convert persona verdicts into `Finding`s
    and `PolicySuggestion`s; this function is a thin wrapper around
    `judge_event` so the Inspector doesn't duplicate prompt logic.
    """
    synthetic = build_synthetic_event(
        agent_id=agent_id,
        source=source,
        system_prompt=system_prompt,
        tools=tools or [],
        ast_summary=ast_summary,
        pattern_matches=pattern_matches or [],
    )
    return await judge_event(
        event=synthetic,
        active_personas=INSPECTOR_PERSONAS,
        mode=JudgeMode.INSPECTOR,
        active_policies=active_policies or [],
        event_id=synthetic["event_id"],
        session_id="",
        agent_id=agent_id,
        component="inspector",
    )
