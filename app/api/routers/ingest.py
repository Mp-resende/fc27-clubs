from __future__ import annotations

import threading

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.api.schemas.ingest import IngestRunInfo
from app.config import Settings
from app.db.models import IngestRun

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["ingest"])

# Thread-safe flag shared between the API (main thread) and the worker thread.
# threading.Event is used instead of asyncio.Event because in "both" mode the
# worker runs its own event loop in a separate thread.
_immediate_trigger: threading.Event | None = None
_trigger_lock = threading.Lock()


def get_trigger_event() -> threading.Event:
    global _immediate_trigger
    with _trigger_lock:
        if _immediate_trigger is None:
            _immediate_trigger = threading.Event()
        return _immediate_trigger


@router.post("/clubs/{club_id}/sync")
async def trigger_sync(
    club_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if club_id != settings.CLUB_ID:
        raise HTTPException(status_code=403, detail="Sync only allowed for configured club")
    get_trigger_event().set()
    log.info("ingest.sync_triggered", club_id=club_id)
    return {"queued": True, "club_id": club_id}


@router.get("/ingest/runs", response_model=dict[str, list[IngestRunInfo]])
def list_ingest_runs(
    limit: int = 20,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    runs = (
        db.query(IngestRun)
        .order_by(IngestRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "runs": [
            IngestRunInfo(
                id=r.id,
                club_id=r.club_id,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                matches_returned=r.matches_returned,
                matches_inserted=r.matches_inserted or 0,
                matches_updated=r.matches_updated or 0,
                error=r.error,
            ).model_dump()
            for r in runs
        ]
    }
