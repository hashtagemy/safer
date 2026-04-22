"""Deterministic PII detection via regex.

Conservative patterns — we prefer false-positives (blocked → user retries)
over false-negatives (PII leaked). Returns a list of PIIMatch objects so
the caller can cite concrete evidence when explaining a block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PIIMatch:
    kind: str  # e.g. "EMAIL", "PHONE", "SSN", "TCKN", "CREDIT_CARD"
    text: str  # the matched substring (for evidence)


# Patterns intentionally conservative.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    # International or US-style phone numbers; 10+ digits with optional
    # separators. Avoids short generic numbers.
    ("PHONE", re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}(?!\d)")),
    # US SSN
    ("SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    # Turkish national ID (TCKN): 11 digits.
    ("TCKN", re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")),
    # Credit-card-ish (Luhn not checked here; we're conservative).
    ("CREDIT_CARD", re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")),
    # IBAN (simple)
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
]


def scan(text: str) -> list[PIIMatch]:
    """Return all PII matches in `text`. Empty list if nothing found."""
    if not text:
        return []
    out: list[PIIMatch] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            match_text = m.group(0).strip()
            key = (kind, match_text)
            if key in seen:
                continue
            seen.add(key)
            out.append(PIIMatch(kind=kind, text=match_text))
    return out


def scan_payload(obj: object) -> list[PIIMatch]:
    """Recursive scan of dict / list / primitives."""
    matches: list[PIIMatch] = []
    if isinstance(obj, str):
        matches.extend(scan(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            matches.extend(scan_payload(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            matches.extend(scan_payload(v))
    return matches
