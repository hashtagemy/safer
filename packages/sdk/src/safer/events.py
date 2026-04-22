"""9-hook lifecycle event models.

These models are the core contract between SDK adapters and the backend.
Every framework adapter converts its native events into one of these 9
hook payloads. Judge / Gateway / Inspector / Session Report all consume
this unified shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, Field


class Hook(str, Enum):
    """9 lifecycle hook points. See CLAUDE.md persona routing rules."""

    ON_SESSION_START = "on_session_start"
    BEFORE_LLM_CALL = "before_llm_call"
    AFTER_LLM_CALL = "after_llm_call"
    BEFORE_TOOL_USE = "before_tool_use"
    AFTER_TOOL_USE = "after_tool_use"
    ON_AGENT_DECISION = "on_agent_decision"
    ON_FINAL_OUTPUT = "on_final_output"
    ON_SESSION_END = "on_session_end"
    ON_ERROR = "on_error"


class RiskHint(str, Enum):
    """Pre-Judge risk signal. Set by Gateway or heuristics."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _event_id() -> str:
    return f"evt_{uuid4().hex[:16]}"


class EventBase(BaseModel):
    """Base fields every event carries."""

    event_id: str = Field(default_factory=_event_id)
    session_id: str
    agent_id: str
    timestamp: datetime = Field(default_factory=_utcnow)
    hook: Hook
    sequence: int = Field(
        description="Monotonic counter within the session, starting at 0"
    )
    risk_hint: RiskHint = RiskHint.LOW
    source: str = Field(
        default="sdk",
        description="Where event came from: sdk | adapter:<name> | otlp | manual",
    )


# ---------- 9 payload subtypes ----------


class OnSessionStartPayload(EventBase):
    hook: Literal[Hook.ON_SESSION_START] = Hook.ON_SESSION_START
    agent_name: str
    agent_version: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class BeforeLLMCallPayload(EventBase):
    hook: Literal[Hook.BEFORE_LLM_CALL] = Hook.BEFORE_LLM_CALL
    model: str
    prompt: str
    tools: list[dict[str, Any]] = Field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None


class AfterLLMCallPayload(EventBase):
    hook: Literal[Hook.AFTER_LLM_CALL] = Hook.AFTER_LLM_CALL
    model: str
    response: str
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class BeforeToolUsePayload(EventBase):
    hook: Literal[Hook.BEFORE_TOOL_USE] = Hook.BEFORE_TOOL_USE
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    previous_context: str | None = None


class AfterToolUsePayload(EventBase):
    hook: Literal[Hook.AFTER_TOOL_USE] = Hook.AFTER_TOOL_USE
    tool_name: str
    result: Any = None
    duration_ms: int = 0
    error: str | None = None


class OnAgentDecisionPayload(EventBase):
    hook: Literal[Hook.ON_AGENT_DECISION] = Hook.ON_AGENT_DECISION
    decision_type: str
    reasoning: str | None = None
    chosen_action: str | None = None


class OnFinalOutputPayload(EventBase):
    hook: Literal[Hook.ON_FINAL_OUTPUT] = Hook.ON_FINAL_OUTPUT
    final_response: str
    total_steps: int = 0


class OnSessionEndPayload(EventBase):
    hook: Literal[Hook.ON_SESSION_END] = Hook.ON_SESSION_END
    total_duration_ms: int = 0
    total_cost_usd: float = 0.0
    success: bool = True


class OnErrorPayload(EventBase):
    hook: Literal[Hook.ON_ERROR] = Hook.ON_ERROR
    error_type: str
    message: str
    stack_trace: str | None = None


Event = Union[
    OnSessionStartPayload,
    BeforeLLMCallPayload,
    AfterLLMCallPayload,
    BeforeToolUsePayload,
    AfterToolUsePayload,
    OnAgentDecisionPayload,
    OnFinalOutputPayload,
    OnSessionEndPayload,
    OnErrorPayload,
]


HOOK_TO_PAYLOAD: dict[Hook, type[EventBase]] = {
    Hook.ON_SESSION_START: OnSessionStartPayload,
    Hook.BEFORE_LLM_CALL: BeforeLLMCallPayload,
    Hook.AFTER_LLM_CALL: AfterLLMCallPayload,
    Hook.BEFORE_TOOL_USE: BeforeToolUsePayload,
    Hook.AFTER_TOOL_USE: AfterToolUsePayload,
    Hook.ON_AGENT_DECISION: OnAgentDecisionPayload,
    Hook.ON_FINAL_OUTPUT: OnFinalOutputPayload,
    Hook.ON_SESSION_END: OnSessionEndPayload,
    Hook.ON_ERROR: OnErrorPayload,
}


def parse_event(raw: dict[str, Any]) -> Event:
    """Discriminated union parser. Raises ValidationError on malformed input."""
    hook = Hook(raw["hook"])
    return HOOK_TO_PAYLOAD[hook].model_validate(raw)
