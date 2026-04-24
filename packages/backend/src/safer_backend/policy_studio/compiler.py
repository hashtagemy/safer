"""Policy Compiler — natural-language policies → Gateway rules.

Design:
- ONE Opus 4.7 call per compile.
- System prompt is cached (`cache_control: ephemeral`) and stable across
  compiles so the second-onward call hits the cache.
- Anthropic deprecated the `temperature` param on modern Claude models;
  we rely on the model's default sampling.
- Output validated by `CompiledPolicy` Pydantic; malformed JSON goes
  through one repair pass before giving up.
- Closed rule-kind whitelist (pii_guard, tool_allowlist, loop_detection,
  regex_block) enforced by the Pydantic validator on `rule_json`.
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
except ImportError:  # pragma: no cover — hard dep
    AsyncAnthropic = None  # type: ignore[assignment,misc]

from ..judge.cost_tracker import record_claude_call
from ..judge.personas import FLAG_VOCABULARY_HINT
from ..models.policies import CompiledPolicy

log = logging.getLogger("safer.policy_studio.compiler")

COMPILER_MODEL = os.environ.get("SAFER_POLICY_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.environ.get("SAFER_POLICY_MAX_TOKENS", "1500"))


# Mirror pricing from judge.engine so cost tracking is consistent.
_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-7": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4-5": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4-5": (0.80, 4.0, 0.08, 1.0),
}


SYSTEM_HEADER = """\
# SAFER Policy Compiler

You translate a natural-language safety policy into a deterministic
JSON rule that the SAFER Gateway can evaluate at runtime.

Output EXACTLY ONE JSON object matching the schema below. No markdown,
no prose, no code fences, no trailing text.
"""

RULE_TAXONOMY = """\
## Rule taxonomy — choose ONE `kind`

### pii_guard
Block tool calls or LLM responses that contain personal data.
{
  "kind": "pii_guard",
  "tools": ["send_email", ...] OR null,   // null = apply to every tool
  "pii_types": ["EMAIL", "SSN", "TCKN", "PHONE", "CREDIT_CARD"] OR null
}

### tool_allowlist
Restrict the agent to an explicit list of approved tools.
{
  "kind": "tool_allowlist",
  "allowed": ["get_order", "get_customer"]
}

### loop_detection
Flag when the same tool is called with identical args too many times.
{
  "kind": "loop_detection",
  "threshold": 3,
  "window_seconds": 60
}

### regex_block
Block whenever a given regex matches any scannable event text
(prompt, final_response, response, tool args).
{
  "kind": "regex_block",
  "pattern": "<Python regex>",
  "flags": ["<closed-vocab flag or custom_...>"]
}

Pick the MOST SPECIFIC kind that captures the intent.
- "Don't email customers" / "no PII to external tools" → pii_guard.
- "Only these N tools" → tool_allowlist.
- "Stop repeating the same action" → loop_detection.
- Natural-language content matching ("no profanity", "no 'ignore previous'")
  → regex_block with a concrete pattern.
"""

GUIDELINES = """\
## Guidelines

- `name` MUST be kebab-case, 3-80 chars, descriptive: "no-email-to-gmail".
- `flag` MUST be one of the closed vocabulary flags OR start with `custom_`.
- `flag_category` must match the flag's category (POLICY is the default for custom flags).
- `severity` reflects user impact:
    LOW     — cosmetic / logging
    MEDIUM  — advisory warnings, soft blocks
    HIGH    — clear policy violation, block recommended
    CRITICAL — exfiltration / code exec / PII egress / unambiguous abuse
- `guard_mode`:
    "monitor"   — log only, do not block
    "intervene" — block only CRITICAL/HIGH events matching this rule (default)
    "enforce"   — block every hit
  Default "intervene". Use "enforce" when the user's language says "never",
  "block", "refuse", or "under no circumstances".
- `code_snippet` is optional. Leave null unless a 3-5 line Python
  helper makes the rule materially clearer to a human reviewer.
- Produce 1-3 `test_cases`:
    * at least one POSITIVE case (expected_block=true) with the exact
      hook/args that trigger the rule;
    * at least one NEGATIVE case (expected_block=false) that looks
      superficially similar but must NOT trigger.
  Each test `event` must include `hook` and whatever fields the rule kind
  inspects (tool_name, args, prompt, final_response).

If the user's request cannot be expressed with these four rule kinds, pick
the closest reasonable one and explain nothing — the JSON is the contract.
"""

OUTPUT_SCHEMA = """\
## Output JSON schema

{
  "name": "<kebab-case>",
  "nl_text": "<verbatim user text>",
  "rule_json": { "kind": "...", ... },
  "code_snippet": "<optional python>" OR null,
  "flag_category": "SECURITY" | "COMPLIANCE" | "TRUST" | "SCOPE" | "ETHICS" | "POLICY" | "OWASP_LLM",
  "flag": "<closed-vocab flag or custom_...>",
  "severity": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "guard_mode": "monitor" | "intervene" | "enforce",
  "test_cases": [
    {
      "description": "<one sentence>",
      "event": { "hook": "before_tool_use", "tool_name": "...", "args": { ... } },
      "expected_block": true | false,
      "expected_flag": "<flag or null>"
    }
  ]
}
"""

FEW_SHOT_EXAMPLES = """\
## Examples

User: "Never let this agent send customer email addresses to any external tool."
→
{
  "name": "no-customer-email-egress",
  "nl_text": "Never let this agent send customer email addresses to any external tool.",
  "rule_json": {"kind": "pii_guard", "tools": null, "pii_types": ["EMAIL"]},
  "code_snippet": null,
  "flag_category": "COMPLIANCE",
  "flag": "pii_sent_external",
  "severity": "HIGH",
  "guard_mode": "enforce",
  "test_cases": [
    {
      "description": "Agent tries to email customer address — must block.",
      "event": {"hook": "before_tool_use", "tool_name": "send_email", "args": {"to": "jane@example.com", "body": "hi"}},
      "expected_block": true,
      "expected_flag": "pii_exposure"
    },
    {
      "description": "Agent looks up an order — no PII egress, must allow.",
      "event": {"hook": "before_tool_use", "tool_name": "get_order", "args": {"id": "123"}},
      "expected_block": false,
      "expected_flag": null
    }
  ]
}

User: "Only allow get_order, get_customer, and refund tools — block everything else."
→
{
  "name": "tool-allowlist-support",
  "nl_text": "Only allow get_order, get_customer, and refund tools — block everything else.",
  "rule_json": {"kind": "tool_allowlist", "allowed": ["get_order", "get_customer", "refund"]},
  "code_snippet": null,
  "flag_category": "POLICY",
  "flag": "unauthorized_tool_call",
  "severity": "HIGH",
  "guard_mode": "enforce",
  "test_cases": [
    {
      "description": "Agent calls approved tool — allow.",
      "event": {"hook": "before_tool_use", "tool_name": "get_order", "args": {"id": "42"}},
      "expected_block": false,
      "expected_flag": null
    },
    {
      "description": "Agent calls off-list tool — must block.",
      "event": {"hook": "before_tool_use", "tool_name": "exec_shell", "args": {"cmd": "ls"}},
      "expected_block": true,
      "expected_flag": "unauthorized_tool_call"
    }
  ]
}
"""


def build_system_prompt() -> str:
    return (
        SYSTEM_HEADER
        + "\n\n"
        + RULE_TAXONOMY
        + "\n\n"
        + FLAG_VOCABULARY_HINT
        + "\n\n"
        + GUIDELINES
        + "\n\n"
        + OUTPUT_SCHEMA
        + "\n\n"
        + FEW_SHOT_EXAMPLES
    )


SYSTEM_PROMPT = build_system_prompt()


# ---------- client management (test-injectable) ----------


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


# ---------- text helpers ----------


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise ValueError("no JSON object found in model response")
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


async def _repair_to_json(client: Any, bad_text: str) -> str:
    repair_prompt = (
        "The previous output was not valid JSON. Re-emit the same compiled "
        "policy as ONE JSON object, no markdown, no prose.\n\nPrevious:\n"
        + bad_text[:4000]
    )
    response = await client.messages.create(
        model=COMPILER_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": repair_prompt}],
    )
    return _extract_text(response)


# ---------- public API ----------


async def compile_policy(nl_text: str) -> CompiledPolicy:
    """Compile a natural-language policy into a `CompiledPolicy`.

    Raises RuntimeError if no Anthropic client is configured.
    """
    nl_text = nl_text.strip()
    if not nl_text:
        raise ValueError("nl_text cannot be empty")

    client = _get_client()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set; policy compiler cannot run."
        )

    user_message = (
        "Compile the following user policy into the JSON schema above.\n"
        "Echo the user's text verbatim in `nl_text`.\n\n"
        f"User policy:\n\"\"\"\n{nl_text}\n\"\"\""
    )

    t0 = time.monotonic()
    response = await client.messages.create(
        model=COMPILER_MODEL,
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

    raw_text = _extract_text(response)
    try:
        data = _extract_json(raw_text)
    except (ValueError, json.JSONDecodeError):
        repaired = await _repair_to_json(client, raw_text)
        data = _extract_json(repaired)

    # Guarantee the user's original text survives even if the model rewrote it.
    data["nl_text"] = data.get("nl_text") or nl_text
    if data.get("nl_text", "").strip() != nl_text:
        # Be generous: if the model paraphrased, prefer the user's actual text.
        data["nl_text"] = nl_text

    compiled = CompiledPolicy.model_validate(data)

    usage = getattr(response, "usage", None)
    tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
    tokens_out = getattr(usage, "output_tokens", 0) if usage else 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) if usage else 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
    cost = _estimate_cost(COMPILER_MODEL, tokens_in, tokens_out, cache_read, cache_write)

    try:
        await record_claude_call(
            component="policy_compiler",
            model=COMPILER_MODEL,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost,
            latency_ms=latency_ms,
        )
    except Exception as e:  # pragma: no cover — cost logging is best-effort
        log.debug("cost tracking failed: %s", e)

    return compiled
