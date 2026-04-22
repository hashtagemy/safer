"""Policy rule evaluator.

Each policy is a JSON rule structure that the engine knows how to evaluate
against a given event. MVP supports three rule kinds; Policy Studio (Phase 9)
compiles natural-language policies into the same shape.

Rule kinds:

  pii_guard:
    { "kind": "pii_guard",
      "tools": ["send_email", ...],    // optional; default: all tools
      "pii_types": ["EMAIL", "SSN"] }  // optional; default: any PII type

  tool_allowlist:
    { "kind": "tool_allowlist",
      "allowed": ["get_order", "get_customer"] }

  loop_detection:
    { "kind": "loop_detection",
      "threshold": 3,                  // calls before detecting loop
      "window_seconds": 60 }

  custom (regex):
    { "kind": "regex_block",
      "pattern": "ignore\\s+previous\\s+instructions",
      "flags": ["custom_no_jailbreak"] }

Every rule produces 0..N PolicyHit objects, each with severity + flag.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..storage.db import get_db
from .pii_regex import scan_payload

# Types


@dataclass(frozen=True)
class PolicyRule:
    policy_id: str
    name: str
    guard_mode: str  # monitor | intervene | enforce
    severity: str  # LOW | MEDIUM | HIGH | CRITICAL
    rule_json: dict[str, Any]


@dataclass
class PolicyHit:
    policy_id: str
    policy_name: str
    severity: str
    flag: str
    evidence: list[str] = field(default_factory=list)
    recommended_mitigation: str | None = None


# ---------- Built-in policies (MVP) ----------

BUILTIN_POLICIES: list[PolicyRule] = [
    PolicyRule(
        policy_id="builtin.pii_guard",
        name="PII Guard",
        guard_mode="intervene",
        severity="HIGH",
        rule_json={"kind": "pii_guard"},
    ),
    PolicyRule(
        policy_id="builtin.tool_allowlist",
        name="Tool Allowlist",
        guard_mode="monitor",
        severity="MEDIUM",
        rule_json={"kind": "tool_allowlist", "allowed": []},  # empty = no restriction
    ),
    PolicyRule(
        policy_id="builtin.loop_detection",
        name="Loop Detection",
        guard_mode="intervene",
        severity="MEDIUM",
        rule_json={"kind": "loop_detection", "threshold": 3, "window_seconds": 60},
    ),
    PolicyRule(
        policy_id="builtin.prompt_injection_guard",
        name="Prompt Injection Guard",
        guard_mode="intervene",
        severity="HIGH",
        rule_json={
            "kind": "regex_block",
            "pattern": r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|messages?)",
            "flags": ["prompt_injection_direct"],
        },
    ),
]


async def load_active_policies(agent_id: str | None = None) -> list[PolicyRule]:
    """Load built-in + user-compiled active policies from the DB."""
    rules: list[PolicyRule] = list(BUILTIN_POLICIES)

    try:
        async with get_db() as db:
            if agent_id is not None:
                q = """
                    SELECT policy_id, name, guard_mode, severity, rule_json
                    FROM policies
                    WHERE active = 1 AND (agent_id IS NULL OR agent_id = ?)
                """
                params: tuple[Any, ...] = (agent_id,)
            else:
                q = """
                    SELECT policy_id, name, guard_mode, severity, rule_json
                    FROM policies
                    WHERE active = 1
                """
                params = ()
            async with db.execute(q, params) as cur:
                async for row in cur:
                    rules.append(
                        PolicyRule(
                            policy_id=row[0],
                            name=row[1],
                            guard_mode=row[2] or "intervene",
                            severity=row[3] or "MEDIUM",
                            rule_json=json.loads(row[4] or "{}"),
                        )
                    )
    except Exception:
        # Before DB is initialized (early boot / tests), just return builtins.
        pass

    return rules


# ---------- Evaluation ----------


def evaluate_rule(rule: PolicyRule, event: dict[str, Any]) -> list[PolicyHit]:
    kind = rule.rule_json.get("kind")
    if kind == "pii_guard":
        return _eval_pii_guard(rule, event)
    if kind == "tool_allowlist":
        return _eval_tool_allowlist(rule, event)
    if kind == "loop_detection":
        # Loop detection needs session history; caller passes recent_tool_calls
        # in the event dict under __recent_tool_calls__.
        return _eval_loop_detection(rule, event)
    if kind == "regex_block":
        return _eval_regex_block(rule, event)
    return []


def evaluate_policies(
    rules: list[PolicyRule], event: dict[str, Any]
) -> list[PolicyHit]:
    hits: list[PolicyHit] = []
    for r in rules:
        hits.extend(evaluate_rule(r, event))
    return hits


# ---------- Individual rule evaluators ----------


def _eval_pii_guard(rule: PolicyRule, event: dict[str, Any]) -> list[PolicyHit]:
    hook = event.get("hook")
    if hook not in ("before_tool_use", "before_llm_call", "on_final_output"):
        return []
    scoped_tools = rule.rule_json.get("tools")
    if hook == "before_tool_use" and scoped_tools:
        if event.get("tool_name") not in scoped_tools:
            return []
    # Scan the relevant payload fields only.
    target = (
        event.get("args")
        if hook == "before_tool_use"
        else event.get("prompt") or event.get("final_response") or ""
    )
    matches = scan_payload(target)
    allow_types = set(rule.rule_json.get("pii_types") or [])
    if allow_types:
        matches = [m for m in matches if m.kind in allow_types]
    if not matches:
        return []
    evidence = [f"{m.kind}: {m.text}" for m in matches[:5]]
    return [
        PolicyHit(
            policy_id=rule.policy_id,
            policy_name=rule.name,
            severity=rule.severity,
            flag="pii_exposure",
            evidence=evidence,
            recommended_mitigation="Mask PII before sending to external tools.",
        )
    ]


def _eval_tool_allowlist(rule: PolicyRule, event: dict[str, Any]) -> list[PolicyHit]:
    if event.get("hook") != "before_tool_use":
        return []
    allowed = rule.rule_json.get("allowed") or []
    if not allowed:
        return []
    tool_name = event.get("tool_name")
    if tool_name in allowed:
        return []
    return [
        PolicyHit(
            policy_id=rule.policy_id,
            policy_name=rule.name,
            severity=rule.severity,
            flag="unauthorized_tool_call",
            evidence=[f"tool={tool_name}"],
            recommended_mitigation=f"Only allowed tools: {', '.join(allowed)}",
        )
    ]


def _eval_loop_detection(rule: PolicyRule, event: dict[str, Any]) -> list[PolicyHit]:
    if event.get("hook") != "before_tool_use":
        return []
    recent: list[dict[str, Any]] = event.get("__recent_tool_calls__") or []
    threshold = int(rule.rule_json.get("threshold", 3))
    tool_name = event.get("tool_name")
    tool_args = json.dumps(event.get("args", {}), sort_keys=True)
    same_count = sum(
        1
        for r in recent
        if r.get("tool_name") == tool_name
        and json.dumps(r.get("args", {}), sort_keys=True) == tool_args
    )
    if same_count + 1 < threshold:
        return []
    return [
        PolicyHit(
            policy_id=rule.policy_id,
            policy_name=rule.name,
            severity=rule.severity,
            flag="loop_detected",
            evidence=[
                f"tool={tool_name}",
                f"identical_recent_calls={same_count + 1}",
            ],
            recommended_mitigation="Break the loop; consider different strategy.",
        )
    ]


def _eval_regex_block(rule: PolicyRule, event: dict[str, Any]) -> list[PolicyHit]:
    pattern = rule.rule_json.get("pattern")
    if not pattern:
        return []
    # Concatenate all scannable text fields.
    parts: list[str] = []
    for key in ("prompt", "final_response", "response"):
        v = event.get(key)
        if isinstance(v, str):
            parts.append(v)
    args = event.get("args")
    if isinstance(args, dict):
        for v in args.values():
            if isinstance(v, str):
                parts.append(v)
    text = "\n".join(parts)
    if not text:
        return []
    try:
        rx = re.compile(pattern)
    except re.error:
        return []
    m = rx.search(text)
    if not m:
        return []
    flags = rule.rule_json.get("flags") or ["policy_violation"]
    return [
        PolicyHit(
            policy_id=rule.policy_id,
            policy_name=rule.name,
            severity=rule.severity,
            flag=flags[0] if flags else "policy_violation",
            evidence=[m.group(0)[:120]],
            recommended_mitigation="Rephrase input to avoid the restricted pattern.",
        )
    ]
