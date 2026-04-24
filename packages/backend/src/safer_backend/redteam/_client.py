"""Shared Anthropic client helpers for Red-Team stages.

Keeping one module means tests inject a client once and every stage
(Strategist, Attacker, Analyst) picks it up. This mirrors the pattern
used by the Judge / Quality / Reconstructor modules.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover
    AsyncAnthropic = None  # type: ignore[assignment,misc]

_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-7": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4-5": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5": (0.80, 4.0, 0.08, 1.0),
}

# Per-stage model selection. Strategist + Attacker stay on Opus 4.7
# because adversarial planning / creativity is demo-critical; Analyst
# drops to Sonnet 4.6 (clustering + OWASP mapping is a structured task
# that Sonnet handles at ~5× lower cost). The legacy
# `SAFER_REDTEAM_MODEL` env var, when set, still overrides all three
# stages — useful for dogfood A/B tests.
_LEGACY_REDTEAM_MODEL = os.environ.get("SAFER_REDTEAM_MODEL")

REDTEAM_STRATEGIST_MODEL = os.environ.get(
    "SAFER_REDTEAM_STRATEGIST_MODEL",
    _LEGACY_REDTEAM_MODEL or "claude-opus-4-7",
)
REDTEAM_ATTACKER_MODEL = os.environ.get(
    "SAFER_REDTEAM_ATTACKER_MODEL",
    _LEGACY_REDTEAM_MODEL or "claude-opus-4-7",
)
REDTEAM_ANALYST_MODEL = os.environ.get(
    "SAFER_REDTEAM_ANALYST_MODEL",
    _LEGACY_REDTEAM_MODEL or "claude-sonnet-4-6",
)

# Backward-compat alias. New code should import the per-stage constant
# above; this keeps existing imports working.
REDTEAM_MODEL = REDTEAM_STRATEGIST_MODEL

_client_singleton: Any = None


def get_client() -> Any:
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


def estimate_cost(
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


def extract_text(response: Any) -> str:
    content = getattr(response, "content", [])
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def extract_json(text: str) -> Any:
    """Return the first JSON value found in `text` (object or array)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    m = _JSON_ARRAY_RE.search(text)
    if m:
        return json.loads(m.group(0))
    raise ValueError("no JSON found in response")


def usage_tuple(response: Any) -> tuple[int, int, int, int]:
    usage = getattr(response, "usage", None)
    return (
        getattr(usage, "input_tokens", 0) if usage else 0,
        getattr(usage, "output_tokens", 0) if usage else 0,
        getattr(usage, "cache_read_input_tokens", 0) if usage else 0,
        getattr(usage, "cache_creation_input_tokens", 0) if usage else 0,
    )
