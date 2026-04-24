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
from .ast_scanner import scan_project
from .pattern_rules import scan_patterns, scan_patterns_project
from .persona_review import review as persona_review
from .persona_review import review_project as persona_review_project
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
    persona_review_mode: str | None = None
    review_skipped = skip_persona_review
    if not skip_persona_review:
        try:
            verdict, persona_review_mode = await _review_persona_with_fallback(
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
        persona_review_mode=persona_review_mode,
    )


async def _review_persona_with_fallback(
    *,
    agent_id: str,
    source: str,
    system_prompt: str,
    tools: list[ToolSpec],
    ast_summary: ASTSummary,
    pattern_matches: list[PatternMatch],
    active_policies: list[dict] | None,
) -> tuple[Verdict, str]:
    """Try Managed Agents first; fall back to the single-call sub-agent.

    Returns (verdict, persona_review_mode) where mode is one of:
      - "managed"                     — Managed Agents succeeded
      - "subagent"                    — Managed Agents unavailable at import
      - "managed_fallback_subagent"   — Managed Agents failed mid-flight
    """
    # Localized import so the legacy path still works if the managed
    # module has a transient import error (e.g. SDK missing in CI).
    try:
        from .managed import InspectorManagedError, review_managed
    except Exception as e:
        log.info("inspector managed path unavailable, using subagent: %s", e)
        verdict = await persona_review(
            agent_id=agent_id,
            source=source,
            system_prompt=system_prompt,
            tools=tools,
            ast_summary=ast_summary,
            pattern_matches=pattern_matches,
            active_policies=active_policies,
        )
        return verdict, "subagent"

    try:
        verdict = await review_managed(
            agent_id=agent_id,
            source=source,
            system_prompt=system_prompt,
            tools=tools,
            ast_summary=ast_summary,
            pattern_matches=pattern_matches,
            active_policies=active_policies,
        )
        return verdict, "managed"
    except InspectorManagedError as e:
        log.warning("Managed Inspector failed, falling back to sub-agent: %s", e)
    except Exception as e:  # pragma: no cover — defensive
        log.exception("Managed Inspector unexpected error, falling back: %s", e)

    verdict = await persona_review(
        agent_id=agent_id,
        source=source,
        system_prompt=system_prompt,
        tools=tools,
        ast_summary=ast_summary,
        pattern_matches=pattern_matches,
        active_policies=active_policies,
    )
    return verdict, "managed_fallback_subagent"


def _build_findings(
    *,
    agent_id: str,
    pattern_matches: list[PatternMatch],
    persona_verdicts: dict[PersonaName, PersonaVerdict],
) -> list[Finding]:
    findings: list[Finding] = []

    for match in pattern_matches:
        category = category_of(match.flag)
        location = (
            f"{match.file_path}:{match.line}" if match.file_path else f"line {match.line}"
        )
        findings.append(
            Finding(
                agent_id=agent_id,
                source=FindingSource.INSPECTOR,
                severity=match.severity,
                category=category.value if category else "UNKNOWN",
                flag=match.flag,
                title=f"{match.rule_id.replace('_', ' ').title()} ({location})",
                description=match.message,
                evidence=[match.snippet] if match.snippet else [],
                reproduction_steps=[
                    f"Scan {match.rule_id} rule against source.",
                    f"See {location}.",
                ],
                file_path=match.file_path,
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
                    persona=persona,
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


async def inspect_project(
    *,
    agent_id: str,
    files: list[tuple[str, str]],
    system_prompt: str = "",
    declared_tools: list[ToolSpec] | None = None,
    active_policies: list[dict] | None = None,
    skip_persona_review: bool = False,
) -> InspectorReport:
    """Project-wide scan — the path triggered by `POST /v1/agents/{id}/scan`.

    Runs AST + deterministic patterns per file, then a single 3-persona
    Opus review over a length-bounded project digest. Findings carry
    their originating file path so the UI can link back to source.
    """
    started = time.monotonic()

    ast_summary = scan_project(files)
    tools_for_report = (
        declared_tools if declared_tools is not None else list(ast_summary.tools)
    )
    pattern_matches = scan_patterns_project(files)

    persona_verdicts: dict[PersonaName, PersonaVerdict] = {}
    persona_review_error: str | None = None
    persona_review_mode: str | None = None
    review_skipped = skip_persona_review
    if not skip_persona_review:
        try:
            verdict, persona_review_mode = await _review_project_with_fallback(
                agent_id=agent_id,
                files=files,
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
        ast_summary=ast_summary,
        pattern_matches=pattern_matches,
        findings=findings,
        persona_verdicts=persona_verdicts,
        policy_suggestions=policy_suggestions,
        scan_mode="project",
        scanned_files=[path for path, _ in files],
        duration_ms=duration_ms,
        persona_review_skipped=review_skipped,
        persona_review_error=persona_review_error,
        persona_review_mode=persona_review_mode,
    )


async def _review_project_with_fallback(
    *,
    agent_id: str,
    files: list[tuple[str, str]],
    system_prompt: str,
    tools: list[ToolSpec],
    ast_summary: ASTSummary,
    pattern_matches: list[PatternMatch],
    active_policies: list[dict] | None,
) -> tuple[Verdict, str]:
    """Project-scan variant of the managed/fallback switch.

    The managed path receives a single concatenated digest of every
    file (same shape `persona_review_project` uses when falling back)
    so the Managed Agent can still grep/read individual sections in its
    sandbox.
    """
    digest = "\n\n".join(
        f"# ===== {path} =====\n{src.rstrip()}\n" for path, src in files
    )

    try:
        from .managed import InspectorManagedError, review_managed
    except Exception as e:
        log.info("inspector managed path unavailable, using subagent: %s", e)
        verdict = await persona_review_project(
            agent_id=agent_id,
            files=files,
            system_prompt=system_prompt,
            tools=tools,
            ast_summary=ast_summary,
            pattern_matches=pattern_matches,
            active_policies=active_policies,
        )
        return verdict, "subagent"

    try:
        verdict = await review_managed(
            agent_id=agent_id,
            source=digest,
            system_prompt=system_prompt,
            tools=tools,
            ast_summary=ast_summary,
            pattern_matches=pattern_matches,
            active_policies=active_policies,
        )
        return verdict, "managed"
    except InspectorManagedError as e:
        log.warning("Managed Inspector failed, falling back to sub-agent: %s", e)
    except Exception as e:  # pragma: no cover — defensive
        log.exception("Managed Inspector unexpected error, falling back: %s", e)

    verdict = await persona_review_project(
        agent_id=agent_id,
        files=files,
        system_prompt=system_prompt,
        tools=tools,
        ast_summary=ast_summary,
        pattern_matches=pattern_matches,
        active_policies=active_policies,
    )
    return verdict, "managed_fallback_subagent"
