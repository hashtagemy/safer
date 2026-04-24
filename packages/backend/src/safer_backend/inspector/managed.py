"""Run the SAFER Inspector as a Claude Managed Agents session.

Drop-in replacement for `persona_review.review()` when the Managed
Agents beta is available. Signature and return type match — callers in
`inspector/orchestrator.py` can switch paths behind an env flag without
changing any downstream code.

Flow:
  1. Bootstrap (cached) the Inspector agent, memory store, and
     environment via `managed_bootstrap`.
  2. Create a session with the memory store attached.
  3. Open the event stream, send the source + metadata as the user
     message, and consume events until `session.status_idle`.
  4. Parse the final assistant JSON message into a `Verdict`.
  5. Record cost via `record_claude_call(component="inspector_managed")`.

The agent writes new patterns into the memory store as a side effect
(system prompt dictates this), so subsequent scans learn from prior
ones.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..judge.cost_tracker import record_claude_call
from ..models.flags import ALL_FLAGS
from ..models.inspector import ASTSummary, PatternMatch, ToolSpec
from ..models.verdicts import (
    Overall,
    PersonaName,
    PersonaVerdict,
    RiskLevel,
    Verdict,
)
from .managed_bootstrap import (
    ManagedBootstrapError,
    _beta_client,
    ensure_environment,
    ensure_inspector_agent,
    ensure_memory_store,
)

log = logging.getLogger("safer.inspector.managed")

INSPECTOR_MODEL = "claude-opus-4-7"
DEFAULT_TIMEOUT_S = 600  # 10 minutes
MEMORY_INSTRUCTIONS = (
    "Shared SAFER Inspector knowledge base. Read `patterns/` BEFORE "
    "analyzing the target. Append genuinely new patterns AFTER. One "
    "file per pattern, grouped by category, <=2KB each."
)


class InspectorManagedError(RuntimeError):
    """Raised when the Managed-Agents Inspector path cannot complete."""


async def review_managed(
    *,
    agent_id: str,
    source: str,
    system_prompt: str = "",
    tools: list[ToolSpec] | None = None,
    ast_summary: ASTSummary,
    pattern_matches: list[PatternMatch] | None = None,
    active_policies: list[dict[str, Any]] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Verdict:
    """Managed-Agents variant of `persona_review.review()`.

    Raises InspectorManagedError on any failure; the orchestrator is
    expected to catch this and fall back to the sub-agent path.
    """
    try:
        client = _beta_client()
    except ManagedBootstrapError as e:
        raise InspectorManagedError(f"beta client unavailable: {e}") from e

    try:
        agent_resource_id = await ensure_inspector_agent(client)
        store_id = await ensure_memory_store(client)
        env_id = await ensure_environment(client)
    except ManagedBootstrapError as e:
        raise InspectorManagedError(f"bootstrap failed: {e}") from e

    user_message = _build_user_message(
        agent_id=agent_id,
        source=source,
        system_prompt=system_prompt,
        tools=tools or [],
        ast_summary=ast_summary,
        pattern_matches=pattern_matches or [],
        active_policies=active_policies or [],
    )

    t0 = time.monotonic()
    try:
        session = await client.beta.sessions.create(
            agent=agent_resource_id,
            environment_id=env_id,
            resources=[
                {
                    "type": "memory_store",
                    "memory_store_id": store_id,
                    "access": "read_write",
                    "instructions": MEMORY_INSTRUCTIONS,
                }
            ],
            title=f"Inspector scan · {agent_id}",
        )
    except Exception as e:
        raise InspectorManagedError(f"sessions.create failed: {e}") from e

    try:
        final_text, usage = await _run_session_to_completion(
            client=client,
            session_id=session.id,
            user_text=user_message,
            timeout_s=timeout_s,
        )
    except InspectorManagedError:
        raise
    except Exception as e:
        raise InspectorManagedError(
            f"session {session.id} stream failed: {e}"
        ) from e

    latency_ms = int((time.monotonic() - t0) * 1000)

    try:
        data = _extract_json(final_text)
    except ValueError as e:
        raise InspectorManagedError(
            f"final message was not valid JSON: {e}"
        ) from e

    # Cost tracking — Managed Agents billing returns tokens in the same
    # fields as messages.create. Session-hour runtime is billed
    # separately by Anthropic and is not reflected here.
    tokens_in = int(usage.get("input_tokens", 0) or 0)
    tokens_out = int(usage.get("output_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cost = _estimate_cost(tokens_in, tokens_out, cache_read, cache_write)

    verdict = _parse_verdict(
        data,
        agent_id=agent_id,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        cost=cost,
    )

    try:
        await record_claude_call(
            component="inspector_managed",
            model=INSPECTOR_MODEL,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost,
            latency_ms=latency_ms,
            agent_id=agent_id,
        )
    except Exception as e:  # pragma: no cover
        log.debug("cost tracking failed: %s", e)

    return verdict


# ---------- internals ----------


def _build_user_message(
    *,
    agent_id: str,
    source: str,
    system_prompt: str,
    tools: list[ToolSpec],
    ast_summary: ASTSummary,
    pattern_matches: list[PatternMatch],
    active_policies: list[dict[str, Any]],
) -> str:
    """Assemble the kickoff user message for the Managed-Agents session.

    We pass the source inline so the agent can `cat > file` it into
    `/workspace/agent_code/`. Metadata and deterministic signals come
    alongside so the persona analyses have concrete anchors.
    """
    header = (
        f"agent_id: {agent_id}\n"
        f"system_prompt:\n---\n{system_prompt.strip()}\n---\n"
        f"declared_tools: {json.dumps([t.model_dump(mode='json') for t in tools])}\n"
        f"ast_summary: {ast_summary.model_dump_json()}\n"
        f"pattern_matches: {json.dumps([m.model_dump(mode='json') for m in pattern_matches])}\n"
        f"active_policies: {json.dumps(active_policies)}\n"
        f"closed_flag_vocabulary: {json.dumps(sorted(ALL_FLAGS))}\n"
    )
    return (
        "You are running a SAFER Inspector scan. Follow your system "
        "prompt's analysis workflow.\n\n"
        "## Metadata\n"
        f"{header}\n"
        "## Source to write and analyze\n"
        "Write the following verbatim to `/workspace/agent_code/agent.py` "
        "using the `file_ops.write` or `bash` tool, then perform your "
        "three-persona review as specified.\n\n"
        "```python\n"
        f"{source}\n"
        "```\n"
    )


async def _run_session_to_completion(
    *,
    client: Any,
    session_id: str,
    user_text: str,
    timeout_s: int,
) -> tuple[str, dict[str, int]]:
    """Open stream, send the kickoff message, return (final_text, usage).

    Accumulates text blocks from `agent.message` events. Stops on
    `session.status_idle`. Raises InspectorManagedError on timeout or
    `session.status_failed`.
    """
    final_parts: list[str] = []
    usage: dict[str, int] = {}

    deadline = time.monotonic() + timeout_s

    # Anthropic async SDK: events.stream(...) is itself `async`, so we
    # have to await the coroutine before entering its context manager.
    # The sync docs show `with client.beta... .stream(id) as s:` — the
    # async form needs the extra `await`.
    async with await client.beta.sessions.events.stream(session_id) as stream:
        await client.beta.sessions.events.send(
            session_id,
            events=[
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": user_text}],
                }
            ],
        )

        async for event in stream:
            if time.monotonic() > deadline:
                raise InspectorManagedError(
                    f"session {session_id} exceeded timeout {timeout_s}s"
                )
            etype = getattr(event, "type", None)
            if etype == "agent.message":
                for block in getattr(event, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        final_parts.append(text)
                # Usage, if surfaced on the message event, is captured.
                ev_usage = getattr(event, "usage", None)
                if ev_usage is not None:
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    ):
                        v = getattr(ev_usage, k, None)
                        if v is not None:
                            usage[k] = usage.get(k, 0) + int(v)
            elif etype == "session.status_failed":
                raise InspectorManagedError(
                    f"session {session_id} reported status_failed"
                )
            elif etype == "session.status_idle":
                break

    return "\n".join(final_parts).strip(), usage


def _extract_json(text: str) -> dict[str, Any]:
    """Find the first `{...}` JSON object in the stream's final text."""
    stripped = text.strip()
    # Fast path: entire message is JSON.
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # Fallback: scan for the first `{` and try progressive slices.
    start = stripped.find("{")
    if start == -1:
        raise ValueError("no JSON object found in assistant final message")
    for end in range(len(stripped), start, -1):
        candidate = stripped[start:end]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON object found in assistant final message")


def _parse_verdict(
    data: dict[str, Any],
    *,
    agent_id: str,
    latency_ms: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_read: int = 0,
    cost: float = 0.0,
) -> Verdict:
    overall_raw = data.get("overall") or {}
    personas_raw = data.get("personas") or {}

    persona_verdicts: dict[PersonaName, PersonaVerdict] = {}
    for key, value in personas_raw.items():
        try:
            persona = PersonaName(key)
        except ValueError:
            log.warning("inspector_managed: unknown persona key %r, skipped", key)
            continue
        if not isinstance(value, dict):
            continue
        value.setdefault("persona", persona.value)
        try:
            persona_verdicts[persona] = PersonaVerdict.model_validate(value)
        except Exception as e:
            raise InspectorManagedError(
                f"PersonaVerdict validation failed for {persona.value}: {e}"
            ) from e

    try:
        overall = Overall(
            risk=RiskLevel(overall_raw.get("risk", "LOW")),
            confidence=float(overall_raw.get("confidence", 0.0) or 0.0),
            block=bool(overall_raw.get("block", False)),
        )
    except Exception as e:
        raise InspectorManagedError(f"Overall validation failed: {e}") from e

    return Verdict(
        event_id=f"ins_{agent_id}",
        session_id="",
        agent_id=agent_id,
        mode="INSPECTOR",
        overall=overall,
        active_personas=list(persona_verdicts.keys()),
        personas=persona_verdicts,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read_tokens=cache_read,
        cost_usd=cost,
    )


# Opus 4.7 pricing per 1M tokens (matches judge/engine.py conventions).
_OPUS_INPUT_USD_PER_MTOKEN = 15.0
_OPUS_OUTPUT_USD_PER_MTOKEN = 75.0
_OPUS_CACHE_READ_USD_PER_MTOKEN = 1.50
_OPUS_CACHE_WRITE_USD_PER_MTOKEN = 18.75


def _estimate_cost(
    tokens_in: int,
    tokens_out: int,
    cache_read: int,
    cache_write: int,
) -> float:
    billable_in = max(0, tokens_in - cache_read - cache_write)
    return (
        billable_in * _OPUS_INPUT_USD_PER_MTOKEN
        + tokens_out * _OPUS_OUTPUT_USD_PER_MTOKEN
        + cache_read * _OPUS_CACHE_READ_USD_PER_MTOKEN
        + cache_write * _OPUS_CACHE_WRITE_USD_PER_MTOKEN
    ) / 1_000_000
