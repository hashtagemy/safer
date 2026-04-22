"""Session Report — per-session health card.

Pipeline (all deterministic Python + two optional Opus calls):

1. Quality Reviewer (Opus) — summarises the full session trace.
2. Thought-Chain Reconstructor (Opus) — narrative + timeline, auto-run
   only when the session contains any HIGH/CRITICAL verdict.
3. Aggregator (pure Python, 0 Claude calls) — fold verdicts + findings
   + quality + reconstructor + cost data into a `SessionReport`.

The API lives under `/v1/sessions/{id}/report` and the router auto-
triggers generation on `on_session_end`.
"""

from __future__ import annotations

from .aggregator import aggregate
from .orchestrator import generate_report

__all__ = ["aggregate", "generate_report"]
