import pathlib
from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Ensure data directory exists
if settings.DB_PATH and not settings.DB_PATH.startswith(":memory:"):
    pathlib.Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def configure_db(conn: Connection) -> None:
    conn.execute(text("PRAGMA journal_mode=WAL"))
    conn.execute(text("PRAGMA busy_timeout=5000"))
    conn.execute(text("PRAGMA foreign_keys=ON"))
    conn.execute(text("PRAGMA synchronous=NORMAL"))


engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _on_connect(dbapi_conn: object, connection_record: object) -> None:  # type: ignore[type-arg]
    from sqlalchemy.engine import Connection as _Conn  # local import to avoid circularity

    # dbapi_conn is the raw sqlite3 connection; wrap via text() calls
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
