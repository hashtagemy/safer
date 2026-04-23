"""Event router — decides which downstream components process each event.

Phase 3: persist + broadcast.
Phase 6: adds Judge dispatch with dynamic persona routing.
Phase 7: adds Gateway (deterministic pre-call) + Haiku per-step + block
broadcast to SDK.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict, deque
from typing import Deque

from safer.events import Event, Hook, RiskHint

from ..gateway import GuardMode, pre_call_check
from ..storage.dao import (
    ingest_agent_register,
    insert_event,
    update_agent_last_seen,
)
from ..storage.db import get_db
from ..ws_broadcaster import broadcaster
from .haiku_prestep import score_step
from .persona_router import is_judge_eligible, select_personas_runtime

# Small rolling log of recent tool calls per session, for loop detection.
_RECENT_TOOL_CALLS: dict[str, Deque[dict[str, object]]] = defaultdict(
    lambda: deque(maxlen=10)
)

log = logging.getLogger("safer.router")


def _judge_active() -> bool:
    """Read the runtime-mutable toggle from runtime_config.

    - "off"  — disabled regardless of key
    - "on"   — forced (may fail if no key, but that's on the operator)
    - "auto" — enabled iff ANTHROPIC_API_KEY is present
    """
    from ..runtime_config import get_judge_enabled

    mode = get_judge_enabled()
    if mode == "off":
        return False
    if mode == "on":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


async def route_event(event: Event) -> None:
    """Route one normalized event."""
    # 0. Onboarding: `on_agent_register` has its own ingest path and does
    # not flow through the runtime pipeline (no Judge, no Gateway, no
    # Haiku, no session_report trigger).
    if event.hook == Hook.ON_AGENT_REGISTER:
        await _handle_agent_register(event)
        return

    # 1. Persist
    try:
        await insert_event(event)
    except Exception as e:
        log.exception("failed to persist event %s: %s", event.event_id, e)
        return

    # Heartbeat — every runtime event pings the agent's last_seen.
    try:
        await update_agent_last_seen(event.agent_id)
    except Exception:  # pragma: no cover — defensive, never block ingestion
        log.debug("update_agent_last_seen failed for %s", event.agent_id)

    event_payload = event.model_dump(mode="json")

    # 2. Track recent tool calls for loop detection.
    if event.hook == Hook.BEFORE_TOOL_USE:
        _RECENT_TOOL_CALLS[event.session_id].append(
            {"tool_name": event_payload.get("tool_name"), "args": event_payload.get("args")}
        )

    # 3. Gateway pre-call — deterministic, cheap, fast.
    gateway_decision = None
    if event.hook in (Hook.BEFORE_TOOL_USE, Hook.BEFORE_LLM_CALL, Hook.ON_FINAL_OUTPUT):
        event_for_policy = dict(event_payload)
        # Inject recent history for loop_detection rules.
        event_for_policy["__recent_tool_calls__"] = list(
            _RECENT_TOOL_CALLS.get(event.session_id, deque())
        )
        try:
            gateway_decision = await pre_call_check(
                event_for_policy, agent_id=event.agent_id
            )
        except Exception as e:
            log.warning("gateway check failed (soft-fail): %s", e)
            gateway_decision = None

        # If Gateway raised an elevated risk, upgrade the event's risk_hint
        # so the Judge router picks a broader persona set.
        if gateway_decision and gateway_decision.risk != "LOW":
            try:
                event = event.model_copy(update={"risk_hint": RiskHint(gateway_decision.risk)})
            except Exception:  # pragma: no cover
                pass

    # 4. Broadcast to dashboard subscribers (include gateway decision).
    broadcast_msg = {
        "type": "event",
        "event_id": event.event_id,
        "session_id": event.session_id,
        "agent_id": event.agent_id,
        "hook": event.hook.value,
        "sequence": event.sequence,
        "timestamp": event.timestamp.isoformat(),
        "risk_hint": event.risk_hint.value,
        "payload": event_payload,
    }
    if gateway_decision is not None:
        broadcast_msg["gateway"] = {
            "decision": gateway_decision.decision,
            "risk": gateway_decision.risk,
            "reason": gateway_decision.reason,
            "hits": [
                {
                    "policy_id": h.policy_id,
                    "policy_name": h.policy_name,
                    "severity": h.severity,
                    "flag": h.flag,
                    "evidence": h.evidence,
                }
                for h in gateway_decision.hits
            ],
        }
    await broadcaster.broadcast(broadcast_msg)

    # 5. Gateway block → emit block signal immediately (SDK raises SaferBlocked).
    if gateway_decision and gateway_decision.is_block:
        await broadcaster.broadcast(
            {
                "type": "block",
                "event_id": event.event_id,
                "session_id": event.session_id,
                "agent_id": event.agent_id,
                "source": "gateway",
                "reason": gateway_decision.reason,
                "risk": gateway_decision.risk,
                "hits": [
                    {"policy_id": h.policy_id, "flag": h.flag}
                    for h in gateway_decision.hits
                ],
            }
        )

    # 6. Per-step Haiku scoring — decision hooks only, fire-and-forget.
    if event.hook in (Hook.BEFORE_LLM_CALL, Hook.BEFORE_TOOL_USE, Hook.ON_AGENT_DECISION):
        asyncio.create_task(_run_haiku(event))

    # 7. Judge dispatch — only on the 3 eligible hooks, and only if active.
    if _judge_active() and is_judge_eligible(event.hook):
        personas = select_personas_runtime(event.hook, event.risk_hint)
        if personas:
            asyncio.create_task(_run_judge(event, [p.value for p in personas]))

    # 8. Session end → generate the Session Report in the background.
    if event.hook == Hook.ON_SESSION_END:
        asyncio.create_task(_run_session_report(event.session_id))


async def _run_haiku(event: Event) -> None:
    """Background Haiku per-step scoring. Never blocks ingestion."""
    try:
        score = await score_step(event.model_dump(mode="json"))
    except Exception as e:  # pragma: no cover
        log.debug("haiku pre-step failed: %s", e)
        return
    if score.relevance_score >= 100 and not score.should_escalate:
        return  # nothing interesting to broadcast
    await broadcaster.broadcast(
        {
            "type": "prestep_score",
            "event_id": event.event_id,
            "session_id": event.session_id,
            "agent_id": event.agent_id,
            "relevance_score": score.relevance_score,
            "should_escalate": score.should_escalate,
            "reason": score.reason,
        }
    )


async def _run_judge(event: Event, active_personas: list[str]) -> None:
    """Background Judge call — does NOT block ingestion."""
    try:
        # Late import so the backend starts even without anthropic installed.
        from ..judge.engine import JudgeMode, judge_event
    except ImportError:  # pragma: no cover
        return

    try:
        verdict = await judge_event(
            event=event.model_dump(mode="json"),
            active_personas=active_personas,
            mode=JudgeMode.RUNTIME,
            event_id=event.event_id,
            session_id=event.session_id,
            agent_id=event.agent_id,
            component="judge",
        )
    except Exception as e:
        log.exception("Judge failed for event %s: %s", event.event_id, e)
        return

    # Persist verdict
    await _persist_verdict(verdict, event)

    # Broadcast verdict to dashboard
    await broadcaster.broadcast(
        {
            "type": "verdict",
            "event_id": event.event_id,
            "session_id": event.session_id,
            "agent_id": event.agent_id,
            "mode": verdict.mode,
            "overall": verdict.overall.model_dump(mode="json"),
            "active_personas": [p.value for p in verdict.active_personas],
            "personas": {
                name.value: pv.model_dump(mode="json")
                for name, pv in verdict.personas.items()
            },
            "latency_ms": verdict.latency_ms,
        }
    )

    # If the verdict says block, also broadcast a BLOCK signal the SDK
    # can consume (SDK raises SaferBlocked in enforce mode).
    if verdict.overall.block:
        await broadcaster.broadcast(
            {
                "type": "block",
                "event_id": event.event_id,
                "session_id": event.session_id,
                "agent_id": event.agent_id,
                "reason": verdict.overall.risk.value,
                "confidence": verdict.overall.confidence,
            }
        )


async def _run_session_report(session_id: str) -> None:
    """Generate + persist the Session Report for a just-ended session.

    Runs fully detached from the ingestion path; a failure here never
    blocks event intake or broadcasting.
    """
    try:
        from ..session_report.orchestrator import generate_report

        report = await generate_report(session_id)
    except Exception as e:  # pragma: no cover — defensive
        log.exception("session report generation failed for %s: %s", session_id, e)
        return
    await broadcaster.broadcast(
        {
            "type": "session_report_ready",
            "session_id": session_id,
            "agent_id": report.agent_id,
            "overall_health": report.overall_health,
            "total_steps": report.total_steps,
        }
    )


async def _persist_verdict(verdict, event: Event) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO verdicts
            (verdict_id, event_id, session_id, agent_id, timestamp, mode,
             overall_risk, overall_confidence, overall_block, active_personas,
             personas_json, latency_ms, tokens_in, tokens_out, cache_read_tokens, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verdict.verdict_id,
                verdict.event_id,
                verdict.session_id,
                verdict.agent_id,
                verdict.timestamp.isoformat(),
                verdict.mode,
                verdict.overall.risk.value,
                verdict.overall.confidence,
                1 if verdict.overall.block else 0,
                json.dumps([p.value for p in verdict.active_personas]),
                json.dumps(
                    {
                        p.value: pv.model_dump(mode="json")
                        for p, pv in verdict.personas.items()
                    }
                ),
                verdict.latency_ms,
                verdict.tokens_in,
                verdict.tokens_out,
                verdict.cache_read_tokens,
                verdict.cost_usd,
            ),
        )
        await db.commit()


async def _handle_agent_register(event: Event) -> None:
    """Persist an `on_agent_register` event into the `agents` table.

    Onboarding events never flow into `events` / `sessions`; they
    describe the agent itself, not a runtime step. The only downstream
    effect is a broadcast so the dashboard can add (or refresh) the
    agent's card without a manual refetch.
    """
    payload = event.model_dump(mode="json")
    try:
        snapshot_changed = await ingest_agent_register(
            agent_id=event.agent_id,
            agent_name=payload.get("agent_name") or event.agent_id,
            framework=payload.get("framework"),
            version=payload.get("agent_version"),
            system_prompt=payload.get("system_prompt"),
            project_root=payload.get("project_root"),
            code_snapshot_b64=payload.get("code_snapshot_b64") or "",
            code_snapshot_hash=payload.get("code_snapshot_hash") or "",
            file_count=int(payload.get("file_count") or 0),
            total_bytes=int(payload.get("total_bytes") or 0),
            truncated=bool(payload.get("snapshot_truncated") or False),
            registered_at=payload.get("registered_at") or event.timestamp.isoformat(),
        )
    except Exception as e:
        log.exception("failed to ingest on_agent_register for %s: %s", event.agent_id, e)
        return

    await broadcaster.broadcast(
        {
            "type": "agent_registered",
            "agent_id": event.agent_id,
            "name": payload.get("agent_name") or event.agent_id,
            "framework": payload.get("framework"),
            "registered_at": payload.get("registered_at")
            or event.timestamp.isoformat(),
            "code_snapshot_hash": payload.get("code_snapshot_hash"),
            "file_count": int(payload.get("file_count") or 0),
            "snapshot_changed": snapshot_changed,
        }
    )
    log.info(
        "agent registered: %s (framework=%s, files=%d, changed=%s)",
        event.agent_id,
        payload.get("framework"),
        int(payload.get("file_count") or 0),
        snapshot_changed,
    )
