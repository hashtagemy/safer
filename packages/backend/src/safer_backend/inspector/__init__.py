"""Inspector — onboarding-phase static review of an agent.

Orchestrator lives in this package's `inspect()` entry point. The scan
combines three layers:

1. `ast_scanner.scan` — deterministic structural facts (tools, LLM call
   sites, entry points, imports).
2. `pattern_rules.scan_patterns` — deterministic security patterns
   (hard-coded creds, shell injection, ssl bypass, eval/exec, ...).
3. `persona_review.review` — ONE Opus call with three personas active
   (Security Auditor, Compliance Officer, Policy Warden) in INSPECTOR
   mode.

Policy suggestions are derived deterministically from the flags emitted
by layers 2 and 3 (see `policy_suggester`).
"""

from __future__ import annotations

# Orchestrator is attached below once all submodules are available.
# Import lazily to avoid a circular import during package load.

__all__ = ["inspect"]


def inspect(*args, **kwargs):  # pragma: no cover — thin forwarder
    from .orchestrator import inspect as _inspect

    return _inspect(*args, **kwargs)
