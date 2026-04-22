"""FastAPI app — SAFER backend entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .ingestion.http import router as http_ingest_router
from .ingestion.ws import router as ws_ingest_router
from .inspector.api import router as inspector_router
from .policy_studio.api import router as policy_studio_router
from .compliance.api import router as compliance_router
from .redteam.api import router as redteam_router
from .session_report.api import router as session_report_router
from .storage.dao import get_cost_summary, get_stats
from .storage.db import init_db
from .ws_broadcaster import broadcaster

log = logging.getLogger("safer.main")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("SAFER backend starting — initializing DB")
    await init_db()
    log.info("SAFER backend ready")
    yield
    log.info("SAFER backend shutting down")


app = FastAPI(
    title="SAFER Backend",
    version="0.1.0",
    lifespan=lifespan,
)

# Dashboard runs on a different origin during dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws_ingest_router)
app.include_router(http_ingest_router)
app.include_router(inspector_router)
app.include_router(policy_studio_router)
app.include_router(session_report_router)
app.include_router(redteam_router)
app.include_router(compliance_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/stats")
async def stats() -> dict:
    return await get_stats()


@app.get("/v1/stats/cost")
async def stats_cost() -> dict:
    return await get_cost_summary()


@app.websocket("/ws/stream")
async def stream(ws: WebSocket) -> None:
    """Dashboard subscribes here to receive live events."""
    await ws.accept()
    await broadcaster.register(ws)
    try:
        while True:
            # We don't expect messages from dashboard; keepalive is fine.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(ws)
