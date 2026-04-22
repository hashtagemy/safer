"""Red-Team Squad data structures.

Three Claude Managed Agents orchestrated:
- Strategist reads target context + seed bank → generates AttackSpec list
- Attacker executes each AttackSpec on the agent → records Attempt
- Analyst clusters Attempts → Findings + OWASP map

Manual mode only (no continuous). Plan B: sub-agent fallback if Managed
Agents API fails.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class AttackCategory(str, Enum):
    DIRECT_INJECTION = "direct_injection"
    INDIRECT_INJECTION = "indirect_injection"
    JAILBREAK = "jailbreak"
    PROMPT_EXTRACTION = "prompt_extraction"
    DATA_EXFILTRATION = "data_exfiltration"
    TOOL_ABUSE = "tool_abuse"
    POLICY_BYPASS = "policy_bypass"


class AttemptResult(str, Enum):
    SUCCESS = "success"  # attack worked, agent broke
    PARTIAL = "partial"  # partially worked
    BLOCKED = "blocked"  # agent resisted


class RedTeamPhase(str, Enum):
    PLANNING = "planning"
    ATTACKING = "attacking"
    ANALYZING = "analyzing"
    DONE = "done"
    FAILED = "failed"


class RedTeamMode(str, Enum):
    MANAGED = "managed"  # Claude Managed Agents API
    SUBAGENT = "subagent"  # Plan B fallback


def _attack_id() -> str:
    return f"atk_{uuid4().hex[:12]}"


def _attempt_id() -> str:
    return f"atp_{uuid4().hex[:12]}"


def _run_id() -> str:
    return f"run_{uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AttackSpec(BaseModel):
    attack_id: str = Field(default_factory=_attack_id)
    category: AttackCategory
    title: str
    prompt: str
    expected_behavior: str = Field(
        description="What the agent SHOULD do (refuse, sanitize, block, etc.)"
    )
    seed_template: str | None = Field(
        default=None, description="Pattern name from seed bank, if used"
    )


class Attempt(BaseModel):
    attempt_id: str = Field(default_factory=_attempt_id)
    run_id: str
    attack_id: str
    result: AttemptResult
    evidence: list[str] = Field(default_factory=list)
    agent_response: str | None = None
    latency_ms: int = 0
    notes: str | None = None
    timestamp: datetime = Field(default_factory=_utcnow)


class RedTeamRun(BaseModel):
    run_id: str = Field(default_factory=_run_id)
    agent_id: str
    mode: RedTeamMode = RedTeamMode.MANAGED
    phase: RedTeamPhase = RedTeamPhase.PLANNING
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    attack_specs: list[AttackSpec] = Field(default_factory=list)
    attempts: list[Attempt] = Field(default_factory=list)
    findings_count: int = 0
    safety_score: int = Field(default=0, ge=0, le=100)
    owasp_map: dict[str, int] = Field(
        default_factory=dict,
        description="owasp_llmNN → count of findings",
    )
    error: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
