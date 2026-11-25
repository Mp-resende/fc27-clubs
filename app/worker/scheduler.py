from __future__ import annotations

import asyncio
import random
from typing import Any

import structlog

from app.config import settings
from app.db.session import SessionLocal
from app.worker.backup import create_backup
from app.worker.circuit_breaker import CircuitBreaker
from app.worker.ingestor import ingest

log = structlog.get_logger(__name__)

_circuit_breaker = CircuitBreaker(
    threshold=settings.CIRCUIT_BREAKER_THRESHOLD,
    backoff_seconds=settings.CIRCUIT_BREAKER_BACKOFF_SECONDS,
)


def _make_ea_client() -> object:
    """Build a real EAClient using the fc-clubs-api library."""
    try:
        from fc_clubs_api import FC27API  # type: ignore[import]

        return FC27API(timeout=settings.EA_TIMEOUT_SECONDS, output="raw")
    except ImportError:
        log.warning("fc_clubs_api not installed; using stub EAClient")

        class _Stub:
            def get_club_matches(
                self, club_id: str, match_type: str, count: int
            ) -> list[dict[str, Any]]:
                return []

        return _Stub()


def _get_active_club_ids(default_club_id: str) -> list[str]:
    """Retrieve all clubs tracked by registered users, or fallback to default."""
    session = SessionLocal()
    try:
        from app.db.models import TrackedClub
        tracked = session.query(TrackedClub.club_id).filter(TrackedClub.is_active == 1).distinct().all()
        cids = [row[0] for row in tracked if row[0]]
        if cids:
            return cids
        return [default_club_id]
    except Exception:
        return [default_club_id]
    finally:
        session.close()


def _run_cycle(club_id: str, client: object) -> None:
    """Execute one ingestion cycle. Never raises."""
    if _circuit_breaker.is_open:
        log.warning("scheduler.skipped_circuit_open", club_id=club_id)
        return

    session = SessionLocal()
    try:
        result = ingest(
            club_id=club_id,
            session=session,
            client=client,  # type: ignore[arg-type]
            parser_version=settings.PARSER_VERSION,
            platform=settings.PLATFORM,
        )
        if result.errors:
            _circuit_breaker.record_failure()
        else:
            _circuit_breaker.record_success()
        log.info(
            "scheduler.cycle_done",
            club_id=club_id,
            returned=result.matches_returned,
            inserted=result.matches_inserted,
            updated=result.matches_updated,
            errors=len(result.errors),
        )
    except Exception as exc:  # noqa: BLE001 - worker must never die
        _circuit_breaker.record_failure()
        log.error("scheduler.cycle_error", club_id=club_id, error=str(exc))
    finally:
        session.close()


async def _backup_loop(interval: int) -> None:
    """Protect the accumulated database with periodic snapshots."""
    # One backup immediately so an existing base is protected right away.
    await asyncio.to_thread(create_backup)
    while True:
        await asyncio.sleep(max(interval, 60))
        await asyncio.to_thread(create_backup)


async def run_scheduler(
    club_id: str,
    interval: int,
    jitter: int,
) -> None:
    """Continuously ingest match data on a jittered interval.

    - The first cycle runs immediately on startup, so data appears at once.
    - Later cycles wait `interval ± jitter` seconds.
    - A manual POST /api/v1/clubs/{club_id}/sync wakes the loop instantly.
    - A background task snapshots the database every BACKUP_INTERVAL_SECONDS.
    """
    log.info("scheduler.start", club_id=club_id, interval=interval, jitter=jitter)
    client = _make_ea_client()

    # Keep the historical database safe (never blocks ingestion).
    # The task runs forever; keeping a reference prevents premature GC.
    backup_task = asyncio.create_task(_backup_loop(settings.BACKUP_INTERVAL_SECONDS))  # noqa: F841

    # Import here to avoid a circular import at module load time
    from app.api.routers.ingest import get_trigger_event

    # First cycle: immediate, so the dashboard is not empty for 5 minutes
    initial_clubs = _get_active_club_ids(club_id)
    log.info("scheduler.first_cycle", clubs=initial_clubs)
    for cid in initial_clubs:
        await asyncio.to_thread(_run_cycle, cid, client)

    while True:
        wait = max(interval + random.randint(-jitter, jitter), 1)
        log.debug("scheduler.sleep", seconds=wait)

        trigger = get_trigger_event()
        deadline = asyncio.get_running_loop().time() + wait
        # Poll the thread-safe flag every second so a manual sync wakes us up
        while not trigger.is_set():
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(1)
        if trigger.is_set():
            trigger.clear()
            log.info("scheduler.immediate_trigger")

        # Run the blocking ingest for all active tracked clubs off the event loop
        cycle_clubs = _get_active_club_ids(club_id)
        for cid in cycle_clubs:
            await asyncio.to_thread(_run_cycle, cid, client)
