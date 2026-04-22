"""Compliance renderer — Jinja2 HTML + JSON + (optional) WeasyPrint PDF.

WeasyPrint is a runtime dependency of this package but not strictly
required; if its native C libraries (cairo/pango) are missing the
import fails at call time and the PDF endpoint returns a 501.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from .data import OWASP_ROWS, ComplianceData, Standard

log = logging.getLogger("safer.compliance.renderer")


_STANDARD_TITLES: dict[Standard, str] = {
    Standard.GDPR: "GDPR Data Protection Report",
    Standard.SOC2: "SOC 2 Trust Services Report",
    Standard.OWASP_LLM: "OWASP LLM Top 10 Report",
}


def _env() -> Environment:
    return Environment(
        loader=PackageLoader("safer_backend", "compliance/templates"),
        autoescape=select_autoescape(("html", "xml", "jinja")),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _template_name(standard: Standard) -> str:
    return f"{standard.value}.html.jinja"


def render_html(data: ComplianceData) -> str:
    env = _env()
    template = env.get_template(_template_name(data.standard))
    return template.render(
        data=data,
        title=_STANDARD_TITLES[data.standard],
        owasp_rows=OWASP_ROWS,
    )


def render_json(data: ComplianceData) -> dict[str, Any]:
    """JSON serialisation of the underlying data. Lossless."""
    return _to_jsonable(dataclasses.asdict(data))


def render_pdf(data: ComplianceData) -> bytes:
    """Render HTML then convert to PDF bytes via WeasyPrint.

    Raises `RuntimeError("weasyprint_unavailable")` if WeasyPrint cannot
    import (missing system libs). Callers should translate to a 501.
    """
    try:
        from weasyprint import HTML  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover — environment-dependent
        raise RuntimeError("weasyprint_unavailable") from e

    html_str = render_html(data)
    pdf_bytes = HTML(string=html_str).write_pdf()
    if not isinstance(pdf_bytes, (bytes, bytearray)):  # pragma: no cover
        raise RuntimeError("weasyprint returned a non-bytes value")
    return bytes(pdf_bytes)


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # datetime / Enum / other
    try:
        return obj.isoformat()  # datetime
    except AttributeError:
        pass
    if hasattr(obj, "value"):  # Enum
        return obj.value
    return str(obj)


# Convenience — the API layer just calls one of these three.
__all__ = ["render_html", "render_json", "render_pdf"]


# Pre-render the JSON helper as a string too, for the API.
def render_json_string(data: ComplianceData) -> str:
    return json.dumps(render_json(data), indent=2)
