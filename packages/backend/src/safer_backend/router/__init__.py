"""Event router — decides which downstream components process each event.

Phase 3: persist + broadcast.
Phase 6: adds Judge dispatch with dynamic persona routing. Gateway + Haiku
pre-filter land in Phase 7.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from safer.events import Event

from ..storage.dao import insert_event
from ..storage.db import get_db
from ..ws_broadcaster import broadcaster
from .persona_router import is_judge_eligible, select_personas_runtime

log = logging.getLogger("safer.router")


JUDGE_ENABLED = os.environ.get("SAFER_JUDGE_ENABLED", "auto").lower()
# auto → enabled iff ANTHROPIC_API_KEY is set; off → disabled; on → forced
# (Phase 6 lands this; Phase 7+ adds Gateway + Haiku in front.)


def _judge_active() -> bool:
    if JUDGE_ENABLED == "off":
        return False
    if JUDGE_ENABLED == "on":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


async def route_event(event: Event) -> None:
    """Route one normalized event."""
    # 1. Persist
    try:
        await insert_event(event)
    except Exception as e:
        log.exception("failed to persist event %s: %s", event.event_id, e)
        return

    # 2. Broadcast to dashboard subscribers
    await broadcaster.broadcast(
        {
            "type": "event",
            "event_id": event.event_id,
            "session_id": event.session_id,
            "agent_id": event.agent_id,
            "hook": event.hook.value,
            "sequence": event.sequence,
            "timestamp": event.timestamp.isoformat(),
            "risk_hint": event.risk_hint.value,
            "payload": event.model_dump(mode="json"),
        }
    )

    # 3. Judge dispatch — only on the 3 eligible hooks, and only if active.
    if _judge_active() and is_judge_eligible(event.hook):
        personas = select_personas_runtime(event.hook, event.risk_hint)
        if personas:
            asyncio.create_task(_run_judge(event, [p.value for p in personas]))


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
