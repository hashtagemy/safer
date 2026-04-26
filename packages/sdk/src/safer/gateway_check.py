"""Synchronous gateway check helper for SDK adapters.

Adapters call `check_or_raise(...)` from inside a `before_*` hook callback
right before they let a tool/LLM/output go through. The helper:

  1. Posts the call shape to the backend's `/v1/gateway/check` endpoint.
  2. Parses the verdict.
  3. Raises `SaferBlocked` if the backend returned `decision="block"`.
  4. Swallows network/timeout/5xx — the contract is "SAFER never blocks
     the host agent on infrastructure failure", only on explicit policy
     decisions. The async event stream will still record the event.

The HTTP roundtrip is intentionally synchronous: the calling adapter is
inside the framework's hook callback (Strands `BeforeToolCallEvent`,
LangChain `on_tool_start`, etc.) and needs a verdict before the framework
moves on. Using `httpx.Client` keeps the dependency footprint identical
to the async transport (httpx is already required).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .client import get_client
from .exceptions import SaferBlocked

log = logging.getLogger("safer.gateway_check")

# Hook field whitelist — these go straight into the request body. The
# backend evaluator only reads these keys, so passing extras would be
# wasted bandwidth.
_HOOK_FIELDS = (
    "tool_name",
    "args",
    "prompt",
    "response",
    "final_response",
)


def check_or_raise(
    hook: str,
    *,
    agent_id: str,
    session_id: str | None = None,
    event_id: str | None = None,
    timeout: float = 2.0,
    **payload: Any,
) -> None:
    """Synchronously ask the backend whether the call should proceed.

    Raises `SaferBlocked` if the backend returns `decision="block"`.
    Returns silently on `allow` / `warn` or any infrastructure error.
    """
    client = get_client()
    if client is None:
        return  # SDK not instrumented — nothing to enforce

    body: dict[str, Any] = {
        "hook": hook,
        "agent_id": agent_id,
    }
    if session_id is not None:
        body["session_id"] = session_id
    for k in _HOOK_FIELDS:
        v = payload.get(k)
        if v is not None:
            body[k] = v

    url = f"{client.config.api_url.rstrip('/')}/v1/gateway/check"
    headers = {"Content-Type": "application/json"}
    if client.config.api_key:
        headers["Authorization"] = f"Bearer {client.config.api_key}"

    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
    except (httpx.HTTPError, OSError) as e:
        log.debug("gateway check network error (allowing): %s", e)
        return

    if resp.status_code >= 500:
        log.warning("gateway check 5xx (allowing): %s", resp.status_code)
        return
    if resp.status_code != 200:
        log.debug("gateway check non-200 (allowing): %s", resp.status_code)
        return

    try:
        data = resp.json()
    except ValueError:
        return

    if data.get("decision") != "block":
        return

    reason = data.get("reason") or "policy violation"
    hits = data.get("hits") or []
    flags = ", ".join(h.get("flag", "") for h in hits if h.get("flag"))
    message = f"Blocked by SAFER policy: {reason}"
    if flags:
        message += f" [{flags}]"

    raise SaferBlocked(
        verdict={"hits": hits, "risk": data.get("risk"), "reason": reason},
        event_id=event_id or "",
        message=message,
    )
