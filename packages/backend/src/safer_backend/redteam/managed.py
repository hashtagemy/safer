"""Red-Team Squad — Claude Managed Agents path.

Three sequential Managed Agents sessions per Red-Team run:

    Strategist (Opus 4.7)  →  AttackSpec[]
        ↓
    Attacker   (Opus 4.7)  →  Attempt[]
        ↓
    Analyst    (Sonnet 4.6) →  findings + safety_score + owasp_map

Each stage runs as its own purpose-built Managed Agent (provisioned
once via `managed_bootstrap`, IDs cached in
`managed_agents_config`). Persistence + websocket phase broadcasts
mirror the subagent path so the dashboard cannot tell the two modes
apart.

If anything fails — beta unavailable, session timeout, malformed JSON
— `attempt_managed` returns `None` so the orchestrator falls back to
the subagent path silently.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..judge.cost_tracker import record_claude_call
from ..models.findings import Finding, FindingSource, Severity
from ..models.flags import ALL_FLAGS, is_known_flag
from ..models.redteam import (
    AttackCategory,
    AttackSpec,
    Attempt,
    AttemptResult,
    RedTeamPhase,
    RedTeamRun,
)
from ._client import (
    REDTEAM_ANALYST_MODEL,
    REDTEAM_ATTACKER_MODEL,
    REDTEAM_STRATEGIST_MODEL,
    estimate_cost,
)
from .managed_bootstrap import (
    ManagedBootstrapError,
    _beta_client,
    ensure_analyst_agent,
    ensure_attacker_agent,
    ensure_environment,
    ensure_strategist_agent,
)

log = logging.getLogger("safer.redteam.managed")

DEFAULT_TIMEOUT_S = 600  # 10 minutes per stage


class ManagedRedTeamError(RuntimeError):
    """Raised when a Managed-Agents Red-Team stage cannot complete."""


# ---------- Public entry point ----------


async def attempt_managed(
    *,
    agent_id: str,
    target_system_prompt: str,
    target_tools: list[str],
    num_attacks: int,
    run: RedTeamRun,
) -> RedTeamRun | None:
    """Run the full 3-stage Red-Team via Managed Agents.

    Returns the populated `RedTeamRun` on success, or `None` to signal
    the orchestrator should fall back to the subagent path.

    Persistence + phase broadcasting are done inline via lazy imports
    from `orchestrator` (which owns those primitives), so the dashboard
    sees the same `redteam_phase` events as on the subagent path.
    """
    from .orchestrator import (
        _broadcast_phase,
        _persist_findings,
        _persist_run,
    )

    # --- Bootstrap (cached after first call) ----------------------
    try:
        client = _beta_client()
        strategist_agent_id = await ensure_strategist_agent(client)
        attacker_agent_id = await ensure_attacker_agent(client)
        analyst_agent_id = await ensure_analyst_agent(client)
        env_id = await ensure_environment(client)
    except ManagedBootstrapError as e:
        log.info("managed red-team bootstrap unavailable: %s", e)
        return None
    except Exception as e:
        log.warning("managed red-team bootstrap failed: %s", e)
        return None

    # --- Stage 1: Strategist --------------------------------------
    try:
        specs = await _run_strategist(
            client=client,
            agent_resource_id=strategist_agent_id,
            env_id=env_id,
            agent_id=agent_id,
            target_system_prompt=target_system_prompt,
            target_tools=target_tools,
            num_attacks=num_attacks,
            run_id=run.run_id,
        )
    except ManagedRedTeamError as e:
        log.warning("managed strategist failed: %s", e)
        return None

    if not specs:
        log.info("managed strategist returned 0 specs; falling back")
        return None

    run.attack_specs = specs
    run.phase = RedTeamPhase.ATTACKING
    await _persist_run(run)
    await _broadcast_phase(run, extra={"attack_count": len(specs)})

    # --- Stage 2: Attacker ----------------------------------------
    try:
        attempts = await _run_attacker(
            client=client,
            agent_resource_id=attacker_agent_id,
            env_id=env_id,
            agent_id=agent_id,
            target_system_prompt=target_system_prompt,
            target_tools=target_tools,
            specs=specs,
            run_id=run.run_id,
        )
    except ManagedRedTeamError as e:
        log.warning("managed attacker failed: %s", e)
        return None

    run.attempts = attempts
    run.phase = RedTeamPhase.ANALYZING
    await _persist_run(run)
    await _broadcast_phase(run, extra={"attempt_count": len(attempts)})

    # --- Stage 3: Analyst -----------------------------------------
    try:
        findings, owasp_map, safety_score = await _run_analyst(
            client=client,
            agent_resource_id=analyst_agent_id,
            env_id=env_id,
            agent_id=agent_id,
            specs=specs,
            attempts=attempts,
            run_id=run.run_id,
        )
    except ManagedRedTeamError as e:
        log.warning("managed analyst failed: %s", e)
        return None

    run.findings_count = len(findings)
    run.owasp_map = owasp_map
    run.safety_score = safety_score
    run.phase = RedTeamPhase.DONE
    run.finished_at = datetime.now(timezone.utc)
    await _persist_run(run)
    await _persist_findings(findings)
    await _broadcast_phase(
        run,
        extra={
            "findings_count": len(findings),
            "safety_score": safety_score,
            "owasp_map": owasp_map,
        },
    )
    return run


# ---------- Stage runners ----------


async def _run_strategist(
    *,
    client: Any,
    agent_resource_id: str,
    env_id: str,
    agent_id: str,
    target_system_prompt: str,
    target_tools: list[str],
    num_attacks: int,
    run_id: str,
) -> list[AttackSpec]:
    user_message = (
        f"agent_id: {agent_id}\n"
        f"run_id: {run_id}\n"
        f"num_attacks: {num_attacks}\n"
        f"target_tools: {json.dumps(target_tools or [])}\n\n"
        "## Target system prompt (verbatim)\n"
        "---\n"
        f"{(target_system_prompt or '').strip()[:4000]}\n"
        "---\n\n"
        "Produce the AttackSpec JSON array now (length must equal "
        f"{num_attacks})."
    )

    parsed, usage, latency_ms = await _run_session(
        client=client,
        agent_resource_id=agent_resource_id,
        env_id=env_id,
        user_text=user_message,
        title=f"redteam-strategist · {agent_id}",
    )

    if not isinstance(parsed, list):
        raise ManagedRedTeamError("strategist did not return a JSON array")

    specs: list[AttackSpec] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        try:
            category = AttackCategory(entry.get("category"))
        except (ValueError, TypeError):
            log.debug("strategist: skipping unknown category: %r", entry.get("category"))
            continue
        try:
            specs.append(
                AttackSpec(
                    category=category,
                    title=str(entry.get("title", "")).strip()
                    or f"{category.value} attack",
                    prompt=str(entry.get("prompt", "")),
                    expected_behavior=str(entry.get("expected_behavior", "")),
                    seed_template=entry.get("seed_template"),
                )
            )
        except Exception as e:  # pragma: no cover
            log.warning("strategist: invalid AttackSpec: %s", e)

    await _record_cost(
        component="redteam_managed",
        model=REDTEAM_STRATEGIST_MODEL,
        usage=usage,
        latency_ms=latency_ms,
        agent_id=agent_id,
        run_id=run_id,
    )
    return specs


async def _run_attacker(
    *,
    client: Any,
    agent_resource_id: str,
    env_id: str,
    agent_id: str,
    target_system_prompt: str,
    target_tools: list[str],
    specs: list[AttackSpec],
    run_id: str,
) -> list[Attempt]:
    specs_payload = [
        {
            "attack_id": s.attack_id,
            "category": s.category.value,
            "title": s.title,
            "prompt": s.prompt,
            "expected_behavior": s.expected_behavior,
        }
        for s in specs
    ]
    user_message = (
        f"agent_id: {agent_id}\n"
        f"run_id: {run_id}\n"
        f"target_tools: {json.dumps(target_tools or [])}\n\n"
        "## Target system prompt\n"
        "---\n"
        f"{(target_system_prompt or '').strip()[:3000]}\n"
        "---\n\n"
        "## AttackSpec list (input order is the required output order)\n"
        f"{json.dumps(specs_payload, ensure_ascii=False)}\n\n"
        "Produce the attempts JSON object now."
    )

    parsed, usage, latency_ms = await _run_session(
        client=client,
        agent_resource_id=agent_resource_id,
        env_id=env_id,
        user_text=user_message,
        title=f"redteam-attacker · {agent_id}",
    )

    if not isinstance(parsed, dict):
        raise ManagedRedTeamError("attacker did not return a JSON object")

    raw_attempts = parsed.get("attempts")
    if not isinstance(raw_attempts, list):
        raise ManagedRedTeamError("attacker JSON missing attempts array")

    by_attack_id: dict[str, dict[str, Any]] = {}
    for entry in raw_attempts:
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("attack_id", ""))
        if aid:
            by_attack_id[aid] = entry

    attempts: list[Attempt] = []
    for spec in specs:
        entry = by_attack_id.get(spec.attack_id)
        if entry is None:
            attempts.append(
                Attempt(
                    run_id=run_id,
                    attack_id=spec.attack_id,
                    result=AttemptResult.BLOCKED,
                    agent_response=None,
                    notes="attacker omitted this AttackSpec; treated as blocked",
                )
            )
            continue

        result_str = str(entry.get("result", "blocked")).lower()
        try:
            result = AttemptResult(result_str)
        except ValueError:
            result = AttemptResult.BLOCKED

        attempts.append(
            Attempt(
                run_id=run_id,
                attack_id=spec.attack_id,
                result=result,
                evidence=[str(e) for e in (entry.get("evidence") or [])][:5],
                agent_response=str(entry.get("simulated_response", ""))[:2000],
                latency_ms=int(entry.get("latency_ms") or 0),
                notes=str(entry.get("notes") or ""),
            )
        )

    await _record_cost(
        component="redteam_managed",
        model=REDTEAM_ATTACKER_MODEL,
        usage=usage,
        latency_ms=latency_ms,
        agent_id=agent_id,
        run_id=run_id,
    )
    return attempts


async def _run_analyst(
    *,
    client: Any,
    agent_resource_id: str,
    env_id: str,
    agent_id: str,
    specs: list[AttackSpec],
    attempts: list[Attempt],
    run_id: str,
) -> tuple[list[Finding], dict[str, int], int]:
    if not attempts:
        return [], {}, 100

    specs_by_id = {s.attack_id: s for s in specs}
    payload: list[dict[str, Any]] = []
    for a in attempts:
        spec = specs_by_id.get(a.attack_id)
        payload.append(
            {
                "attack_id": a.attack_id,
                "category": spec.category.value if spec else "unknown",
                "prompt": (spec.prompt if spec else "")[:1000],
                "expected_behavior": (spec.expected_behavior if spec else "")[:500],
                "result": a.result.value,
                "simulated_response": (a.agent_response or "")[:1000],
                "evidence": list(a.evidence)[:3],
                "notes": (a.notes or "")[:300],
            }
        )

    user_message = (
        f"agent_id: {agent_id}\n"
        f"run_id: {run_id}\n"
        f"total_attempts: {len(attempts)}\n"
        f"closed_flag_vocabulary: {json.dumps(sorted(ALL_FLAGS))}\n\n"
        "## Attempts\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Produce the analyst report JSON now."
    )

    parsed, usage, latency_ms = await _run_session(
        client=client,
        agent_resource_id=agent_resource_id,
        env_id=env_id,
        user_text=user_message,
        title=f"redteam-analyst · {agent_id}",
    )

    if not isinstance(parsed, dict):
        raise ManagedRedTeamError("analyst did not return a JSON object")

    findings: list[Finding] = []
    for entry in parsed.get("findings") or []:
        if not isinstance(entry, dict):
            continue
        flag = str(entry.get("flag", ""))
        if not flag or not is_known_flag(flag):
            flag = "custom_redteam_finding"
        try:
            findings.append(
                Finding(
                    agent_id=agent_id,
                    source=FindingSource.RED_TEAM,
                    severity=Severity(str(entry.get("severity", "MEDIUM"))),
                    category=str(entry.get("category", "SECURITY")),
                    flag=flag,
                    title=str(entry.get("title", "Red-Team finding"))[:200],
                    description=str(entry.get("description", ""))[:1000],
                    evidence=[str(e) for e in (entry.get("evidence") or [])][:5],
                    reproduction_steps=[
                        str(s) for s in (entry.get("reproduction_steps") or [])
                    ][:10],
                    recommended_mitigation=entry.get("recommended_mitigation"),
                    owasp_id=entry.get("owasp_id"),
                )
            )
        except Exception as e:  # pragma: no cover
            log.warning("analyst: invalid Finding: %s", e)

    owasp_map = {
        str(k): int(v)
        for k, v in (parsed.get("owasp_map") or {}).items()
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit())
    }

    safety_score = int(parsed.get("safety_score", 0))
    safety_score = max(0, min(100, safety_score))

    await _record_cost(
        component="redteam_managed",
        model=REDTEAM_ANALYST_MODEL,
        usage=usage,
        latency_ms=latency_ms,
        agent_id=agent_id,
        run_id=run_id,
    )
    return findings, owasp_map, safety_score


# ---------- Generic session runner ----------


async def _run_session(
    *,
    client: Any,
    agent_resource_id: str,
    env_id: str,
    user_text: str,
    title: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[Any, dict[str, int], int]:
    """Open a Managed Agents session, send the kickoff message, return
    `(parsed_json, usage, latency_ms)`.

    Raises ManagedRedTeamError on any failure (incl. non-JSON final
    message). The orchestrator turns that into a fallback signal.
    """
    t0 = time.monotonic()
    try:
        session = await client.beta.sessions.create(
            agent=agent_resource_id,
            environment_id=env_id,
            title=title,
        )
    except Exception as e:
        raise ManagedRedTeamError(f"sessions.create failed: {e}") from e

    session_id = getattr(session, "id", None) or (
        session.get("id") if isinstance(session, dict) else None
    )
    if not session_id:
        raise ManagedRedTeamError("sessions.create returned no id")

    try:
        final_text, usage = await _stream_session(
            client=client,
            session_id=session_id,
            user_text=user_text,
            timeout_s=timeout_s,
        )
    except ManagedRedTeamError:
        raise
    except Exception as e:
        raise ManagedRedTeamError(f"session {session_id} stream failed: {e}") from e

    latency_ms = int((time.monotonic() - t0) * 1000)

    try:
        parsed = _extract_json(final_text)
    except ValueError as e:
        raise ManagedRedTeamError(
            f"final message was not valid JSON: {e}"
        ) from e

    return parsed, usage, latency_ms


async def _stream_session(
    *,
    client: Any,
    session_id: str,
    user_text: str,
    timeout_s: int,
) -> tuple[str, dict[str, int]]:
    final_parts: list[str] = []
    usage: dict[str, int] = {}
    deadline = time.monotonic() + timeout_s

    # Same pattern as inspector/managed.py: async stream() returns a
    # coroutine that must be awaited before entering the context.
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
                raise ManagedRedTeamError(
                    f"session {session_id} exceeded timeout {timeout_s}s"
                )
            etype = getattr(event, "type", None)
            if etype == "agent.message":
                for block in getattr(event, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        final_parts.append(text)
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
                raise ManagedRedTeamError(
                    f"session {session_id} reported status_failed"
                )
            elif etype == "session.status_idle":
                break

    return "\n".join(final_parts).strip(), usage


def _extract_json(text: str) -> Any:
    """Find the first JSON value (object OR array) in `text`.

    Mirrors `inspector/managed._extract_json` but accepts top-level
    arrays too because the Strategist returns a JSON array. Prefers
    whichever opener (`{` or `[`) appears first in the text — so prose
    surrounding a top-level array doesn't get sliced down to one of
    the array's nested objects.
    """
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    obj_start = stripped.find("{")
    arr_start = stripped.find("[")

    candidates: list[tuple[int, str, str]] = []
    if obj_start != -1:
        candidates.append((obj_start, "{", "}"))
    if arr_start != -1:
        candidates.append((arr_start, "[", "]"))
    candidates.sort(key=lambda c: c[0])  # earliest opener wins

    for start, opener, closer in candidates:
        end = stripped.rfind(closer)
        if end == -1 or end < start:
            continue
        for stop in range(end + 1, start, -1):
            candidate = stripped[start:stop]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON value found in assistant final message")


async def _record_cost(
    *,
    component: str,
    model: str,
    usage: dict[str, int],
    latency_ms: int,
    agent_id: str,
    run_id: str,
) -> None:
    tokens_in = int(usage.get("input_tokens", 0) or 0)
    tokens_out = int(usage.get("output_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cost = estimate_cost(model, tokens_in, tokens_out, cache_read, cache_write)
    try:
        await record_claude_call(
            component=component,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost,
            latency_ms=latency_ms,
            agent_id=agent_id,
            session_id=run_id,
        )
    except Exception as e:  # pragma: no cover
        log.debug("cost tracking failed: %s", e)
