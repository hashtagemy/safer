"""Event router — decides which downstream components process each event.

Skeleton in Phase 3; fully populated in Phase 6 (Judge) + Phase 7 (Gateway + Haiku).
For now, the router just persists events and broadcasts them; dispatch
to Judge/Gateway comes later.
"""

from __future__ import annotations

import logging

from safer.events import Event

from ..storage.dao import insert_event
from ..ws_broadcaster import broadcaster

log = logging.getLogger("safer.router")


async def route_event(event: Event) -> None:
    """Route one normalized event.

    Phase 3: persist + broadcast.
    Phase 6+: call Gateway (pre-call) → Haiku pre-step (at decision hooks)
             → Judge (dynamic persona set) → possibly emit block signal.
    """
    try:
        await insert_event(event)
    except Exception as e:
        log.exception("failed to persist event %s: %s", event.event_id, e)
        return
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
            # Full payload for dashboards that want it:
            "payload": event.model_dump(mode="json"),
        }
    )
