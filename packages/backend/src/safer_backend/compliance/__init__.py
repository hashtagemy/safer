"""Compliance Pack — time-range reports for GDPR / SOC2 / OWASP LLM.

Given a start/end date and a `standard`, the data loader pulls the
relevant agents, sessions, findings, and gateway/judge decisions from
the DB; the renderer turns that into HTML (Jinja2), JSON, or PDF
(WeasyPrint). Zero Claude calls in the report build itself — all the
LLM work has already been done by the Judge / Inspector / Red-Team
pipelines and persisted.
"""

from __future__ import annotations

from .data import ComplianceData, Standard, load_range
from .renderer import render_html, render_json, render_pdf

__all__ = [
    "ComplianceData",
    "Standard",
    "load_range",
    "render_html",
    "render_json",
    "render_pdf",
]
