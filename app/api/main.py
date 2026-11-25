from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import (
    analytics,
    auth,
    championships,
    clubs,
    export,
    health,
    ingest,
    matches,
    payments,
    players,
    public_ea,
    review,
)
from app.dashboard.routes import router as dashboard_router

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log.info("api.startup")
    yield
    log.info("api.shutdown")


app = FastAPI(
    title="FC27 Clubs API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("api.unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(payments.router)
app.include_router(public_ea.router)
app.include_router(clubs.router)
app.include_router(championships.router)
app.include_router(matches.router)
app.include_router(players.router)
app.include_router(analytics.router)
app.include_router(review.router)
app.include_router(ingest.router)
app.include_router(export.router)
app.include_router(dashboard_router)

import pathlib

_static_dir = pathlib.Path(__file__).parent.parent / "dashboard" / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
