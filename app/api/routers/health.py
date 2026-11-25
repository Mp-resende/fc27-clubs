from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import IngestRun

log = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict[str, object]:
    db_reachable = False
    last_ingest: str | None = None
    last_ingest_ok = False

    try:
        db.execute(text("SELECT 1"))
        db_reachable = True
    except Exception as exc:
        log.warning("health.db_unreachable", error=str(exc))

    try:
        run = (
            db.query(IngestRun)
            .filter(IngestRun.status == "success")
            .order_by(IngestRun.finished_at.desc())
            .first()
        )
        if run is not None:
            last_ingest = run.finished_at
            if run.finished_at:
                finished_dt = datetime.fromisoformat(run.finished_at.replace("Z", "+00:00"))
                now = datetime.now(tz=timezone.utc)
                diff_minutes = (now - finished_dt).total_seconds() / 60
                last_ingest_ok = diff_minutes <= 30
    except Exception as exc:
        log.warning("health.ingest_check_error", error=str(exc))

    status = "ok" if db_reachable and last_ingest_ok else "degraded"
    return {
        "status": status,
        "last_ingest": last_ingest,
        "last_ingest_ok": last_ingest_ok,
        "db_reachable": db_reachable,
    }
