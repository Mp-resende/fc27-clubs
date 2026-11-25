from __future__ import annotations

import json
import pathlib
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db import models  # noqa: F401 – register all ORM models

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """In-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def sample_match_raw() -> dict[str, object]:
    """Load the sample match JSON fixture."""
    data = (FIXTURES_DIR / "sample_match.json").read_text(encoding="utf-8")
    return json.loads(data)  # type: ignore[return-value]


@pytest.fixture()
def ea_client_mock(sample_match_raw: dict[str, object]) -> object:
    """Mock EAClient that returns the sample match payload."""

    class _MockClient:
        def get_club_matches(
            self, club_id: str, match_type: str, count: int
        ) -> list[dict[str, object]]:
            return [sample_match_raw]

    return _MockClient()
