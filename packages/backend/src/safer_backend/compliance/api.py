"""FastAPI router for Compliance Pack.

One endpoint — `POST /v1/reports/build` — returns the rendered report
in the requested format (application/json, text/html, application/pdf).

Stateless: no report_id, no DB storage. The underlying DB data is the
source of truth, and a fresh build against the same range/standard is
reproducible.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from .data import Standard, load_range
from .renderer import render_html, render_json_string, render_pdf

router = APIRouter(prefix="/v1/reports", tags=["compliance"])


class BuildRequest(BaseModel):
    standard: Literal["gdpr", "soc2", "owasp_llm"]
    start_date: datetime = Field(description="ISO date or datetime; date → start-of-day UTC")
    end_date: datetime = Field(description="ISO date or datetime; date → end-of-day UTC")
    format: Literal["html", "pdf", "json"] = "html"
    agent_id: str | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _coerce_dates(cls, v):
        # Accept YYYY-MM-DD as well as full ISO timestamps.
        if isinstance(v, str) and len(v) == 10:
            return datetime.combine(datetime.fromisoformat(v).date(), time.min).replace(
                tzinfo=timezone.utc
            )
        return v


def _filename(standard: str, fmt: str, start: datetime, end: datetime) -> str:
    return (
        f"safer-{standard}-{start.date().isoformat()}-to-"
        f"{end.date().isoformat()}.{fmt}"
    )


@router.post("/build")
async def build_report(req: BuildRequest):
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")

    # If only a date was supplied for end_date, extend to end-of-day UTC.
    end = req.end_date
    if end.time() == time.min:
        end = datetime.combine(end.date(), time.max).replace(tzinfo=timezone.utc)
    start = req.start_date
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    data = await load_range(
        start=start,
        end=end,
        standard=Standard(req.standard),
        agent_id=req.agent_id,
    )

    filename = _filename(req.standard, req.format, start, end)

    if req.format == "json":
        # Return the raw dict so FastAPI/Pydantic handles the encoding.
        from .renderer import render_json

        return JSONResponse(
            content=render_json(data),
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    if req.format == "html":
        html = render_html(data)
        return HTMLResponse(
            content=html,
            headers={"Content-Disposition": f"inline; filename={filename}"},
        )

    # format == "pdf"
    try:
        pdf = render_pdf(data)
    except RuntimeError as e:
        if str(e) == "weasyprint_unavailable":
            raise HTTPException(
                status_code=501,
                detail=(
                    "PDF rendering requires WeasyPrint's system libraries "
                    "(cairo, pango). Install them or use the HTML format."
                ),
            ) from e
        raise
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# Expose JSON string helper to tests.
__all__ = ["router", "render_json_string"]
