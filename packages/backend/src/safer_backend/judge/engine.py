"""Judge engine — Anthropic Opus 4.7 call with prompt caching + JSON repair.

Design:
- ONE Opus call per event (or per Inspector code scan)
- System prompt (all 6 personas + flag vocab + schema) is cached
  (`cache_control: ephemeral`) — identical bytes across all calls so hits
  happen within the 5-min window
- Temperature=0 for deterministic classification
- Pydantic validates the JSON output; on malformed output we try one
  targeted repair pass before giving up
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from enum import Enum
from typing import Any

try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover — should be a hard dep
    AsyncAnthropic = None  # type: ignore[assignment,misc]

from ..models.verdicts import (
    Overall,
    PersonaName,
    PersonaVerdict,
    RiskLevel,
    Verdict,
)
from .cost_tracker import record_claude_call
from .personas import SYSTEM_PROMPT

log = logging.getLogger("safer.judge.engine")

JUDGE_MODEL = os.environ.get("SAFER_JUDGE_MODEL", "claude-opus-4-7")
MAX_TOKENS = int(os.environ.get("SAFER_JUDGE_MAX_TOKENS", "2000"))


def _current_max_tokens() -> int:
    """Runtime-mutable via /v1/config; falls back to the env-seeded value."""
    try:
        from ..runtime_config import get_judge_max_tokens

        return get_judge_max_tokens()
    except Exception:  # pragma: no cover — tests that don't import runtime_config
        return MAX_TOKENS

# Pricing per 1M tokens (USD). Must mirror sdk/adapters/claude_sdk._PRICING.
_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-7": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4-5": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5": (0.80, 4.0, 0.08, 1.0),
}


class JudgeMode(str, Enum):
    RUNTIME = "RUNTIME"
    INSPECTOR = "INSPECTOR"


def _estimate_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    cache_read: int,
    cache_write: int,
) -> float:
    p_in, p_out, p_cr, p_cw = _PRICING.get(model, _PRICING["claude-opus-4-7"])
    billable_in = max(0, tokens_in - cache_read - cache_write)
    return (
        (billable_in * p_in)
        + (tokens_out * p_out)
        + (cache_read * p_cr)
        + (cache_write * p_cw)
    ) / 1_000_000


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction — in case the model wraps JSON in prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise ValueError("no JSON object found in model response")
    return json.loads(m.group(0))


_client_singleton: Any = None


def _get_client() -> Any:
    """Return an AsyncAnthropic client, or None if not configured."""
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    if AsyncAnthropic is None:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    _client_singleton = AsyncAnthropic()
    return _client_singleton


def set_client(client: Any) -> None:
    """Dependency injection for tests."""
    global _client_singleton
    _client_singleton = client


def _build_user_message(
    *,
    mode: JudgeMode,
    active_personas: list[str],
    event: dict[str, Any],
    active_policies: list[dict[str, Any]] | None = None,
) -> str:
    """Build the per-call user message that specifies what to evaluate.

    Keep this structured and compact — the system prompt explains the rules.
    """
    payload = {
        "mode": mode.value,
        "active_personas": active_personas,
        "active_policies": active_policies or [],
        "event": event,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def judge_event(
    *,
    event: dict[str, Any],
    active_personas: list[str],
    mode: JudgeMode = JudgeMode.RUNTIME,
    active_policies: list[dict[str, Any]] | None = None,
    event_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
    component: str = "judge",
) -> Verdict:
    """Run the Multi-Persona Judge over a single event.

    Raises RuntimeError if the Anthropic client is not configured.
    """
    client = _get_client()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set; Judge cannot run. "
            "Set it in .env for real operation."
        )

    user_message = _build_user_message(
        mode=mode,
        active_personas=active_personas,
        event=event,
        active_policies=active_policies,
    )

    t0 = time.monotonic()
    response = await client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=_current_max_tokens(),
        temperature=0,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    raw_text = _extract_text(response)
    try:
        data = _extract_json(raw_text)
    except (ValueError, json.JSONDecodeError):
        # One repair pass — ask the model to re-emit strict JSON.
        repaired = await _repair_to_json(client, raw_text)
        data = _extract_json(repaired)

    usage = getattr(response, "usage", None)
    tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
    tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
    cost = _estimate_cost(JUDGE_MODEL, tokens_in, tokens_out, cache_read, cache_write)

    # Track cost fire-and-forget (best-effort).
    try:
        await record_claude_call(
            component=component,
            model=JUDGE_MODEL,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost,
            latency_ms=latency_ms,
            agent_id=agent_id,
            session_id=session_id,
            event_id=event_id,
        )
    except Exception as e:  # pragma: no cover
        log.debug("cost tracking failed: %s", e)

    return _parse_verdict(
        data=data,
        event_id=event_id or event.get("event_id", ""),
        session_id=session_id or event.get("session_id", ""),
        agent_id=agent_id or event.get("agent_id", ""),
        mode=mode,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        cost=cost,
    )


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", [])
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


async def _repair_to_json(client: Any, bad_text: str) -> str:
    """One-shot repair: ask the model to re-emit valid JSON only."""
    repair_prompt = (
        "The previous output was not valid JSON. Re-emit the same verdict "
        "as a SINGLE JSON object, no markdown, no prose.\n\nPrevious:\n"
        + bad_text[:4000]
    )
    response = await client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=_current_max_tokens(),
        temperature=0,
        messages=[{"role": "user", "content": repair_prompt}],
    )
    return _extract_text(response)


def _parse_verdict(
    *,
    data: dict[str, Any],
    event_id: str,
    session_id: str,
    agent_id: str,
    mode: JudgeMode,
    latency_ms: int,
    tokens_in: int,
    tokens_out: int,
    cache_read: int,
    cost: float,
) -> Verdict:
    overall_raw = data.get("overall", {})
    overall = Overall(
        risk=RiskLevel(overall_raw.get("risk", "LOW")),
        confidence=float(overall_raw.get("confidence", 0.0)),
        block=bool(overall_raw.get("block", False)),
    )
    active_personas_raw = data.get("active_personas", [])
    active_personas = [PersonaName(p) for p in active_personas_raw]
    personas_raw = data.get("personas", {})
    personas: dict[PersonaName, PersonaVerdict] = {}
    for name_str, pv_raw in personas_raw.items():
        try:
            name = PersonaName(name_str)
        except ValueError:
            log.warning("unknown persona in verdict: %s", name_str)
            continue
        # Skip silently-unknown flags; validator will reject the whole PersonaVerdict
        # if any flag is unknown, so pre-filter to known + custom_ prefix.
        flags = pv_raw.get("flags") or []
        from ..models.flags import is_known_flag

        clean_flags = [f for f in flags if is_known_flag(f)]
        if len(clean_flags) != len(flags):
            log.debug(
                "dropped unknown flags in persona %s: %s",
                name_str,
                [f for f in flags if not is_known_flag(f)],
            )
        try:
            personas[name] = PersonaVerdict(
                persona=name,
                score=int(pv_raw.get("score", 100)),
                confidence=float(pv_raw.get("confidence", 0.0)),
                flags=clean_flags,
                evidence=list(pv_raw.get("evidence") or []),
                reasoning=str(pv_raw.get("reasoning", "")),
                recommended_mitigation=pv_raw.get("recommended_mitigation"),
            )
        except Exception as e:  # pragma: no cover
            log.warning("invalid PersonaVerdict for %s: %s", name_str, e)

    return Verdict(
        event_id=event_id,
        session_id=session_id,
        agent_id=agent_id,
        mode=mode.value,
        active_personas=active_personas,
        personas=personas,
        overall=overall,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read_tokens=cache_read,
        cost_usd=cost,
        raw=data,
    )
