"""Gateway — deterministic pre-call enforcement.

Runs BEFORE the Judge on every before_llm_call + before_tool_use event.
Uses regex PII detection + active policy rules. Fast (<10ms), cheap
(no Claude call in the hot path), and deterministic.

Three guard modes (SAFER_GUARD_MODE env or per-agent):
    monitor   — log everything, never block
    intervene — block only CRITICAL (used for prod-ready defaults)
    enforce   — block any policy violation at all
"""

from .engine import Decision, GuardMode, pre_call_check
from .pii_regex import PIIMatch, scan as scan_pii
from .policy_engine import PolicyRule, evaluate_policies, load_active_policies

__all__ = [
    "Decision",
    "GuardMode",
    "pre_call_check",
    "PIIMatch",
    "scan_pii",
    "PolicyRule",
    "evaluate_policies",
    "load_active_policies",
]
