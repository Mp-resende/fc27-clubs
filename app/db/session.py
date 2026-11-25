import pathlib
from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

import os
import pathlib
from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Determine database URL: PostgreSQL takes precedence over SQLite if configured
raw_db_url = os.environ.get("DATABASE_URL") or settings.DATABASE_URL

if raw_db_url:
    # Modern SQLAlchemy requires postgresql:// instead of legacy postgres://
    if raw_db_url.startswith("postgres://"):
        raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)
    
    engine = create_engine(
        raw_db_url,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=False,
    )
else:
    # Default SQLite local storage
    if settings.DB_PATH and not settings.DB_PATH.startswith(":memory:"):
        pathlib.Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{settings.DB_PATH}",
        connect_args={"check_same_thread": False},
        echo=False,
    )


def configure_db(conn: Connection) -> None:
    if engine.dialect.name == "sqlite":
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=5000"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))


if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: object, connection_record: object) -> None:  # type: ignore[type-arg]
        # dbapi_conn is the raw sqlite3 connection; wrap via execute calls
        dbapi_conn.execute("PRAGMA journal_mode=WAL")  # type: ignore[attr-defined]
        dbapi_conn.execute("PRAGMA busy_timeout=5000")  # type: ignore[attr-defined]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]
        dbapi_conn.execute("PRAGMA synchronous=NORMAL")  # type: ignore[attr-defined]


SessionLocal: sessionmaker[Session] = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
