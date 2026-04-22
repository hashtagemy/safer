"""Red-Team Squad — manual, three-stage adversarial evaluation.

Two execution modes:

- `subagent` (default, MVP) — plain Opus calls for Strategist,
  Attacker, and Analyst. Works everywhere.
- `managed` (stretch) — Anthropic Managed Agents API. Any failure
  transparently falls back to `subagent`.

Seed bank: 7 attack categories × 6 seeds = 42 templates. The
Strategist customises them for the target agent; the Attacker
role-plays the target under attack; the Analyst clusters attempts
into findings with an OWASP LLM Top 10 map.

Never continuous. Only kicks off via an explicit API call.
"""

from __future__ import annotations

from .orchestrator import run_redteam

__all__ = ["run_redteam"]
