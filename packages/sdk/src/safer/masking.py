"""Credential masking — regex-based, applied at SDK transport and backend log.

Conservative patterns that match common secret formats. Any match becomes
`<REDACTED:type>`. Applied recursively across event payloads as JSON.
"""

from __future__ import annotations

import re
from typing import Any

# ---------- Patterns ----------
# Order matters — more specific patterns first.

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Anthropic keys
    ("ANTHROPIC_KEY", re.compile(r"sk-ant-[A-Za-z0-9_\-]{32,}")),
    # OpenAI keys
    ("OPENAI_KEY", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    # AWS access keys
    ("AWS_KEY", re.compile(r"AKIA[0-9A-Z]{16}")),
    # GitHub tokens
    ("GITHUB_TOKEN", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    # Bearer tokens in Authorization headers
    ("BEARER", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*")),
    # Generic long hex / base64 secrets preceded by secret-y keywords
    (
        "GENERIC_SECRET",
        re.compile(
            r"(?i)(?:password|passwd|secret|api[_\-]?key|token|auth|credential)"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{16,})['\"]?"
        ),
    ),
    # Private key headers
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def mask(text: str) -> str:
    """Mask credentials in a single string. Idempotent."""
    if not text:
        return text
    out = text
    for name, pattern in _PATTERNS:
        out = pattern.sub(f"<REDACTED:{name}>", out)
    return out


def mask_payload(obj: Any) -> Any:
    """Recursively mask strings in dict / list / primitive payloads."""
    if isinstance(obj, str):
        return mask(obj)
    if isinstance(obj, dict):
        return {k: mask_payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(mask_payload(x) for x in obj)
    return obj
