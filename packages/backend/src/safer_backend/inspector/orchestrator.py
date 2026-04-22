"""Inspector orchestrator — ties AST + patterns + persona review together.

One call, one `InspectorReport`. Deterministic layers always run; the
Claude-powered persona review is skipped (with a note in the report) if
the Anthropic client cannot be initialised. That keeps demos runnable
without an API key and keeps tests hermetic.
"""

from __future__ import annotations

import logging
import time

from ..models.findings import Finding, FindingSource, Severity
from ..models.flags import category_of
from ..models.inspector import (
    ASTSummary,
    InspectorReport,
    PatternMatch,
    ToolSpec,
)
from ..models.verdicts import PersonaName, PersonaVerdict, Verdict
from .ast_scanner import scan as scan_ast
from .pattern_rules import scan_patterns
from .persona_review import review as persona_review
from .policy_suggester import suggest_policies

log = logging.getLogger("safer.inspector")


_SEVERITY_PENALTY: dict[Severity, int] = {
    Severity.LOW: 2,
    Severity.MEDIUM: 6,
    Severity.HIGH: 14,
    Severity.CRITICAL: 28,
}

_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


async def inspect(
    *,
    agent_id: str,
    source: str,
    system_prompt: str = "",
    declared_tools: list[ToolSpec] | None = None,
    active_policies: list[dict] | None = None,
    module_name: str = "",
    skip_persona_review: bool = False,
) -> InspectorReport:
    """Run all three layers and return the combined report.

    - `declared_tools` takes precedence over AST-discovered tools. When
      omitted, the AST scanner's tool list is used.
    - `skip_persona_review=True` forces the deterministic-only path.
      Also triggered automatically if the Judge engine has no client.
    """
    started = time.monotonic()

    # --- Layer 1: AST ---
    ast_summary = scan_ast(source, module_name=module_name)
    tools_for_report = declared_tools if declared_tools is not None else list(ast_summary.tools)

    # --- Layer 2: deterministic patterns ---
    pattern_matches = scan_patterns(source)

    # --- Layer 3: 3-persona Judge (INSPECTOR mode) ---
    persona_verdicts: dict[PersonaName, PersonaVerdict] = {}
    persona_review_error: str | None = None
    review_skipped = skip_persona_review
    if not skip_persona_review:
        try:
            verdict: Verdict = await persona_review(
                agent_id=agent_id,
                source=source,
                system_prompt=system_prompt,
                tools=tools_for_report,
                ast_summary=ast_summary,
                pattern_matches=pattern_matches,
                active_policies=active_policies,
            )
            persona_verdicts = dict(verdict.personas)
        except RuntimeError as e:
            log.warning("persona review skipped — %s", e)
            review_skipped = True
            persona_review_error = str(e)
        except Exception as e:  # pragma: no cover — defensive
            log.exception("persona review failed")
            review_skipped = True
            persona_review_error = f"{type(e).__name__}: {e}"

    findings = _build_findings(
        agent_id=agent_id,
        pattern_matches=pattern_matches,
        persona_verdicts=persona_verdicts,
    )

    policy_suggestions = suggest_policies(
        persona_verdicts={k.value: v for k, v in persona_verdicts.items()},
        pattern_matches=pattern_matches,
    )

    risk_score = _compute_risk_score(pattern_matches, persona_verdicts)
    risk_level = _risk_level_for(pattern_matches, persona_verdicts)

    duration_ms = int((time.monotonic() - started) * 1000)

    return InspectorReport(
        agent_id=agent_id,
        risk_score=risk_score,
        risk_level=risk_level,
        ast_summary=ASTSummary(
            module=ast_summary.module,
            tools=tools_for_report,
            llm_calls=ast_summary.llm_calls,
            entry_points=ast_summary.entry_points,
            imports=ast_summary.imports,
            loc=ast_summary.loc,
            parse_error=ast_summary.parse_error,
        ),
        pattern_matches=pattern_matches,
        findings=findings,
        persona_verdicts=persona_verdicts,
        policy_suggestions=policy_suggestions,
        duration_ms=duration_ms,
        persona_review_skipped=review_skipped,
        persona_review_error=persona_review_error,
    )


def _build_findings(
    *,
    agent_id: str,
    pattern_matches: list[PatternMatch],
    persona_verdicts: dict[PersonaName, PersonaVerdict],
) -> list[Finding]:
    findings: list[Finding] = []

    for match in pattern_matches:
        category = category_of(match.flag)
        findings.append(
            Finding(
                agent_id=agent_id,
                source=FindingSource.INSPECTOR,
                severity=match.severity,
                category=category.value if category else "UNKNOWN",
                flag=match.flag,
                title=f"{match.rule_id.replace('_', ' ').title()} (line {match.line})",
                description=match.message,
                evidence=[match.snippet] if match.snippet else [],
                reproduction_steps=[
                    f"Scan {match.rule_id} rule against source.",
                    f"See line {match.line}.",
                ],
            )
        )

    for persona, verdict in persona_verdicts.items():
        severity = _persona_score_to_severity(verdict.score)
        for flag in verdict.flags:
            category = category_of(flag)
            findings.append(
                Finding(
                    agent_id=agent_id,
                    source=FindingSource.INSPECTOR,
                    severity=severity,
                    category=category.value if category else "UNKNOWN",
                    flag=flag,
                    title=f"{persona.value.replace('_', ' ').title()}: {flag}",
                    description=verdict.reasoning or f"{persona.value} flagged '{flag}'.",
                    evidence=list(verdict.evidence),
                    reproduction_steps=[],
                    recommended_mitigation=verdict.recommended_mitigation,
                )
            )

    return findings


def _compute_risk_score(
    pattern_matches: list[PatternMatch],
    persona_verdicts: dict[PersonaName, PersonaVerdict],
) -> int:
    """Combine deterministic + persona signals into a 0-100 score.

    - Start at 100.
    - Subtract per-match severity penalties from patterns.
    - Take min(that, lowest persona score) so a single persona-flagged
      critical issue can't be masked by a clean AST.
    """
    pattern_score = 100
    for match in pattern_matches:
        pattern_score -= _SEVERITY_PENALTY.get(match.severity, 0)
    pattern_score = max(0, min(100, pattern_score))

    if persona_verdicts:
        persona_score = min(v.score for v in persona_verdicts.values())
        final = min(pattern_score, persona_score)
    else:
        final = pattern_score

    return max(0, min(100, final))


def _risk_level_for(
    pattern_matches: list[PatternMatch],
    persona_verdicts: dict[PersonaName, PersonaVerdict],
) -> Severity:
    """Pick the worst severity observed across all layers.

    Using the worst individual finding (rather than an aggregate score)
    means one CRITICAL issue drives the overall level to CRITICAL even
    if everything else is clean — which is what the user needs to see.
    """
    highest = Severity.LOW
    for match in pattern_matches:
        if _SEVERITY_ORDER[match.severity] > _SEVERITY_ORDER[highest]:
            highest = match.severity
    for verdict in persona_verdicts.values():
        if not verdict.flags:
            continue
        sev = _persona_score_to_severity(verdict.score)
        if _SEVERITY_ORDER[sev] > _SEVERITY_ORDER[highest]:
            highest = sev
    return highest


def _persona_score_to_severity(score: int) -> Severity:
    """Map a persona score (0-100) to a finding severity."""
    if score >= 80:
        return Severity.LOW
    if score >= 60:
        return Severity.MEDIUM
    if score >= 30:
        return Severity.HIGH
    return Severity.CRITICAL
