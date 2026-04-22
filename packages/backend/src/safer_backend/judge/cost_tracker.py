"""Cost tracking for Claude calls — powers the live credit counter."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from ..storage.db import get_db


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _call_id() -> str:
    return f"call_{uuid4().hex[:16]}"


async def record_claude_call(
    *,
    component: str,
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    agent_id: str | None = None,
    session_id: str | None = None,
    event_id: str | None = None,
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO claude_calls
            (call_id, timestamp, component, model, tokens_in, tokens_out,
             cache_read_tokens, cache_write_tokens, cost_usd, latency_ms,
             agent_id, session_id, event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _call_id(),
                _utcnow_iso(),
                component,
                model,
                tokens_in,
                tokens_out,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
                latency_ms,
                agent_id,
                session_id,
                event_id,
            ),
        )
        await db.commit()
