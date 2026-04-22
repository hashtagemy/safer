"""Multi-Persona Judge — 6 personas, dynamic routing, single Opus call.

See CLAUDE.md and Phase 6 for design details. Key rules:
- Runtime Judge only runs on 3 hooks (before_tool_use, on_agent_decision,
  on_final_output) — see router/persona_router.py
- Every Opus call uses prompt cache (~3k token system prompt is cacheable)
- Temperature=0 for deterministic classification
- Dual-mode personas: INSPECTOR (code scan) vs RUNTIME (live events)
"""

from .cost_tracker import record_claude_call
from .engine import judge_event, JudgeMode

__all__ = ["judge_event", "JudgeMode", "record_claude_call"]
