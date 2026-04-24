"""Thought-Chain Reconstructor — one Opus 4.7 call per session.

Reads the event trace and returns:
  - narrative: a short human-readable story of what happened.
  - timeline:  a list of (step, hook, risk, summary) the UI can render.

Called automatically for sessions with any HIGH / CRITICAL verdict; the
session report layer decides. Pure Python callers can still invoke it
manually for "lazy" reconstruction on-demand.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover
    AsyncAnthropic = None  # type: ignore[assignment,misc]

from ..judge.cost_tracker import record_claude_call
from ..models.reconstructor import ThoughtChain
from ..models.session_report import TimelineEntry
from ..storage.db import get_db

log = logging.getLogger("safer.reconstructor")

RECON_MODEL = os.environ.get("SAFER_RECON_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.environ.get("SAFER_RECON_MAX_TOKENS", "2000"))

_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-7": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4-5": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5": (0.80, 4.0, 0.08, 1.0),
}


SYSTEM_PROMPT = """\
# SAFER Thought-Chain Reconstructor

You reconstruct what an agent did during a session, as if narrating it
for an auditor. You never invent steps and you never pad the story; the
event trace is the only source of truth.

## Output (strict JSON)

Return ONE JSON object, no markdown, no prose outside the object:

{
  "narrative": "<2-5 short paragraphs, plain English, past tense>",
  "timeline": [
    {
      "step": <int sequence>,
      "hook": "<hook name>",
      "risk": "LOW|MEDIUM|HIGH|CRITICAL",
      "summary": "<one sentence>"
    }
  ]
}

Guidelines:
- Narrative — one short paragraph per logical phase of the session
  (start → tool use → decision → final output → end). Reference
  specific tool calls or facts; do NOT speculate about intent you
  cannot read from the trace.
- Timeline — one entry per *important* step, not every hook. Skip
  trivial `after_*` echoes. Include anything with risk ≥ MEDIUM or
  that changed the session's direction.
- Use event `sequence` numbers verbatim for `step`.
- If the trace is empty, return a narrative saying so and an empty
  timeline.
"""


_client_singleton: Any = None


def _get_client() -> Any:
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


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError("no JSON object found in Reconstructor response")
    return json.loads(m.group(0))


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", [])
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


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


_LARGE_KEYS_TO_TRIM: frozenset[str] = frozenset(
    {"prompt", "response", "final_response", "result", "text", "content"}
)


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, str) and k in _LARGE_KEYS_TO_TRIM and len(v) > 400:
            out[k] = v[:400] + "…"
        elif isinstance(v, dict):
            out[k] = _compact(v)
        else:
            out[k] = v
    return out


async def _load_trace(session_id: str) -> tuple[str, list[dict[str, Any]]]:
    async with get_db() as db:
        async with db.execute(
            "SELECT agent_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise ValueError(f"session {session_id} not found")
        agent_id = row[0]

        events: list[dict[str, Any]] = []
        async with db.execute(
            """
            SELECT sequence, hook, risk_hint, payload_json
            FROM events
            WHERE session_id = ?
            ORDER BY sequence ASC
            """,
            (session_id,),
        ) as cur:
            async for r in cur:
                try:
                    payload = json.loads(r[3]) if r[3] else {}
                except json.JSONDecodeError:
                    payload = {}
                events.append(
                    {
                        "sequence": r[0],
                        "hook": r[1],
                        "risk": r[2],
                        "payload": _compact(payload),
                    }
                )
    return agent_id, events


async def reconstruct(session_id: str) -> ThoughtChain:
    """Build a thought chain for the session. Raises if no client."""
    agent_id, events = await _load_trace(session_id)

    client = _get_client()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set; Thought-Chain Reconstructor cannot run."
        )

    user_message = (
        f"session_id: {session_id}\n"
        f"agent_id: {agent_id}\n"
        f"event_count: {len(events)}\n\n"
        "## Events\n"
        + json.dumps(events, ensure_ascii=False, indent=2)
    )

    t0 = time.monotonic()
    response = await client.messages.create(
        model=RECON_MODEL,
        max_tokens=MAX_TOKENS,
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

    raw = _extract_text(response)
    data = _extract_json(raw)

    timeline: list[TimelineEntry] = []
    for entry in data.get("timeline") or []:
        try:
            timeline.append(
                TimelineEntry(
                    step=int(entry.get("step", 0)),
                    hook=str(entry.get("hook", "")),
                    risk=str(entry.get("risk", "LOW")),
                    summary=str(entry.get("summary", "")),
                )
            )
        except Exception:
            # Silently drop malformed entries — narrative still survives.
            continue

    chain = ThoughtChain(
        session_id=session_id,
        agent_id=agent_id,
        narrative=str(data.get("narrative", "")),
        timeline=timeline,
        latency_ms=latency_ms,
    )

    usage = getattr(response, "usage", None)
    tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
    tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
    chain.cost_usd = _estimate_cost(
        RECON_MODEL, tokens_in, tokens_out, cache_read, cache_write
    )

    try:
        await record_claude_call(
            component="reconstructor",
            model=RECON_MODEL,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=chain.cost_usd,
            latency_ms=latency_ms,
            agent_id=agent_id,
            session_id=session_id,
        )
    except Exception as e:  # pragma: no cover
        log.debug("cost tracking failed: %s", e)

    return chain
