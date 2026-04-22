"""FastAPI router for Session Report: generate + get."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models.session_report import SessionReport
from .orchestrator import generate_report, load_cached_report

router = APIRouter(prefix="/v1/sessions", tags=["session_report"])


@router.get("/{session_id}/report", response_model=SessionReport)
async def get_report(session_id: str) -> SessionReport:
    """Return the cached report if present; otherwise generate and persist."""
    try:
        cached = await load_cached_report(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if cached is not None:
        return cached
    try:
        return await generate_report(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{session_id}/report/generate", response_model=SessionReport)
async def post_generate(
    session_id: str, force_reconstruct: bool = False
) -> SessionReport:
    """Force (re)generation of the report."""
    try:
        return await generate_report(
            session_id, force_reconstruct=force_reconstruct
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
