"""Entry point for running the application from the command line.

Usage (execute from the project root, where this file lives):
    python __main__.py worker   -> start the background ingest scheduler
    python __main__.py api      -> start the FastAPI + Uvicorn server
    python __main__.py both     -> start both in the same process (threads)
    python __main__.py sync     -> run one full ingest cycle and print a summary
"""

from __future__ import annotations

import asyncio
import sys
import threading


def _run_api() -> None:
    import os
    import uvicorn

    from app.config import settings

    port = int(os.environ.get("PORT") or settings.SERVER_PORT)
    host = os.environ.get("SERVER_HOST") or "0.0.0.0"

    uvicorn.run(
        "app.api.main:app",
        host=host,
        port=port,
        reload=False,
    )


def _run_worker() -> None:
    from app.config import settings
    from app.db.base import Base
    from app.db.session import engine
    from app.worker.scheduler import run_scheduler

    # Ensure tables exist before the worker starts
    Base.metadata.create_all(engine)

    asyncio.run(
        run_scheduler(
            club_id=settings.CLUB_ID,
            interval=settings.INGEST_INTERVAL_SECONDS,
            jitter=settings.INGEST_JITTER_SECONDS,
        )
    )


def _run_sync() -> None:
    """Run a single ingestion cycle (all match types + season snapshot)."""
    from app.config import settings
    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    from app.worker.ingestor import ingest
    from app.worker.scheduler import _make_ea_client

    Base.metadata.create_all(engine)

    session = SessionLocal()
    try:
        result = ingest(
            club_id=settings.CLUB_ID,
            session=session,
            client=_make_ea_client(),  # type: ignore[arg-type]
            parser_version=settings.PARSER_VERSION,
            platform=settings.PLATFORM,
        )
    finally:
        session.close()

    print(f"Partidas retornadas: {result.matches_returned}")
    print(f"Novas: {result.matches_inserted} · Atualizadas: {result.matches_updated}")
    print(
        f"Perfil EA: {'ok' if result.profile_synced else 'falhou'} · "
        f"membros: {result.members_synced}"
    )
    if result.profile_error:
        print(f"Erro no perfil: {result.profile_error}")
    for error in result.errors:
        print(f"Erro: {error}")


def main() -> None:
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ],
        cache_logger_on_first_use=True,
    )

    import os
    from app.config import settings
    from app.db.base import Base
    from app.db.session import engine

    os.makedirs(os.path.dirname(settings.DB_PATH) or "data", exist_ok=True)
    os.makedirs(settings.BACKUP_DIR, exist_ok=True)
    os.makedirs(settings.EVIDENCE_DIR, exist_ok=True)

    Base.metadata.create_all(engine)

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "api":
        _run_api()
    elif command == "worker":
        _run_worker()
    elif command == "both":
        worker_thread = threading.Thread(target=_run_worker, daemon=True, name="worker")
        worker_thread.start()
        _run_api()
    elif command == "sync":
        _run_sync()
    else:
        print(f"Unknown command: {command!r}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
