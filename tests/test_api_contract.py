from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.main import app
from app.api.deps import get_db
from app.db.base import Base
from app.db import models  # noqa: F401
from app.worker.ingestor import ingest

PARSER_VERSION = "parser-3.0"


@pytest.fixture()
def db_engine():  # type: ignore[no-untyped-def]
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # garante que todas as conexões in-memory compartilhem o mesmo banco
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db_with_data(db_engine, sample_match_raw, ea_client_mock):  # type: ignore[no-untyped-def]
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    ingest("682077", session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    session.close()

    def _get_db_override():  # type: ignore[no-untyped-def]
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_ok(db_with_data: None) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "db_reachable" in data


@pytest.mark.asyncio
async def test_get_match_has_both_clubs(db_with_data: None) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/matches/1000000000001")
    assert resp.status_code == 200
    data = resp.json()
    club_ids = {p["club_id"] for p in data["players"]}
    assert "682077" in club_ids
    assert "999999" in club_ids


@pytest.mark.asyncio
async def test_player_rating_null_displayed(db_with_data: None) -> None:
    """The substitute player (0 seconds) must have rating=null, not 0.0."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/matches/1000000000001")
    assert resp.status_code == 200
    data = resp.json()
    sub = next((p for p in data["players"] if p["player_key"] == "player_a5"), None)
    assert sub is not None, "SubPlayer should be in response"
    assert sub["rating"] is None, f"Expected None, got {sub['rating']}"


@pytest.mark.asyncio
async def test_match_result_calculated(db_with_data: None) -> None:
    """club_matches result must be derived from goals, not raw EA flags."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/clubs/682077/matches")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    # 682077 scored 3 vs 1 → is_win
    assert item["is_win"] == 1
    assert item["is_draw"] == 0
    assert item["is_loss"] == 0
