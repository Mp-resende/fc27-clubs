from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.main import app
from app.db import models  # noqa: F401 – register all ORM models
from app.db.base import Base
from app.db.models import Match, MatchClassification
from app.worker.ingestor import ingest

PARSER_VERSION = "parser-3.0"
CLUB_ID = "682077"
MATCH_ID = "1000000000001"
SECOND_MATCH_ID = "1000000000002"


@pytest.fixture()
def db_engine():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture()
def session_factory(db_engine):  # type: ignore[no-untyped-def]
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture()
def two_match_client(sample_match_raw: dict):  # type: ignore[no-untyped-def]
    second = json.loads(json.dumps(sample_match_raw))
    second["matchId"] = SECOND_MATCH_ID

    class _Client:
        def get_club_matches(
            self, club_id: str, match_type: str, count: int
        ) -> list[dict[str, object]]:
            return [sample_match_raw, second]

    return _Client()


@pytest.fixture()
def db_with_data(session_factory, ea_client_mock):  # type: ignore[no-untyped-def]
    session: Session = session_factory()
    ingest(CLUB_ID, session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    session.close()

    def _override():  # type: ignore[no-untyped-def]
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def db_with_two_matches(session_factory, two_match_client):  # type: ignore[no-untyped-def]
    session: Session = session_factory()
    ingest(CLUB_ID, session, two_match_client, PARSER_VERSION)  # type: ignore[arg-type]
    assert session.query(Match).count() == 2
    session.close()

    def _override():  # type: ignore[no-untyped-def]
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create_championship(
    client: AsyncClient,
    name: str = "Liga dos Amigos",
    championship_format: str = "league",
) -> dict:
    response = await client.post(
        "/api/v1/championships",
        json={"name": name, "format": championship_format, "status": "active"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_create_and_list_championship(db_with_data: None) -> None:
    async with _client() as client:
        created = await _create_championship(client)
        assert created["id"] > 0
        assert created["name"] == "Liga dos Amigos"
        assert created["matches"] == 0
        assert created["campaign"]["played"] == 0

        listed = await client.get("/api/v1/championships")
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == created["id"]


@pytest.mark.asyncio
async def test_classification_is_one_to_one(
    db_with_data: None, session_factory
) -> None:
    async with _client() as client:
        first = await _create_championship(client, "Liga A")
        second = await _create_championship(client, "Copa B")

        response = await client.post(
            f"/api/v1/matches/{MATCH_ID}/classification",
            json={"championship_id": first["id"], "stage": "Fase de grupos"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["championship_name"] == "Liga A"

        # Reclassifying replaces the previous link (1:1).
        response = await client.post(
            f"/api/v1/matches/{MATCH_ID}/classification",
            json={"championship_id": second["id"], "round": "Final"},
        )
        assert response.status_code == 200
        detail = await client.get(f"/api/v1/matches/{MATCH_ID}")
        payload = detail.json()
        assert payload["classification"]["championship_id"] == second["id"]
        assert payload["classification"]["championship_name"] == "Copa B"
        assert payload["classification"]["round"] == "Final"

    session = session_factory()
    try:
        assert session.query(MatchClassification).count() == 1
    finally:
        session.close()


@pytest.mark.asyncio
async def test_bulk_classify_and_campaign(db_with_two_matches: None) -> None:
    async with _client() as client:
        championship = await _create_championship(client)

        response = await client.post(
            "/api/v1/matches/bulk-classify",
            json={
                "match_ids": [MATCH_ID, SECOND_MATCH_ID, "missing-match"],
                "championship_id": championship["id"],
                "stage": "Fase de grupos",
                "tags": ["clássico", "rodada 1"],
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["classified"] == 2
        assert result["skipped"] == ["missing-match"]

        detail = await client.get(f"/api/v1/championships/{championship['id']}")
        assert detail.status_code == 200
        data = detail.json()
        # Both sample matches are 3-1 wins for the configured club.
        assert data["campaign"]["played"] == 2
        assert data["campaign"]["wins"] == 2
        assert data["campaign"]["points"] == 6
        assert data["campaign"]["goals_for"] == 6
        assert data["campaign"]["goals_against"] == 2
        assert data["campaign"]["points_pct"] == 100.0

        matches = await client.get(
            f"/api/v1/championships/{championship['id']}/matches"
        )
        assert matches.status_code == 200
        items = matches.json()["items"]
        assert len(items) == 2
        assert all(item["championship"]["stage"] == "Fase de grupos" for item in items)
        assert items[0]["championship"]["tags"] == ["clássico", "rodada 1"]

        scorers = matches.json()["top_scorers"]
        assert scorers, "top scorers should be calculated from classified matches"


@pytest.mark.asyncio
async def test_delete_is_blocked_while_classified(
    db_with_data: None,
) -> None:
    async with _client() as client:
        championship = await _create_championship(client)
        await client.post(
            f"/api/v1/matches/{MATCH_ID}/classification",
            json={"championship_id": championship["id"]},
        )

        blocked = await client.delete(f"/api/v1/championships/{championship['id']}")
        assert blocked.status_code == 409
        assert "classificada" in blocked.json()["detail"]

        unlinked = await client.delete(
            f"/api/v1/matches/{MATCH_ID}/classification"
        )
        assert unlinked.status_code == 204

        deleted = await client.delete(f"/api/v1/championships/{championship['id']}")
        assert deleted.status_code == 204

        listed = await client.get("/api/v1/championships")
        assert listed.json()["items"] == []


@pytest.mark.asyncio
async def test_classification_unknown_championship_returns_404(
    db_with_data: None,
) -> None:
    async with _client() as client:
        response = await client.post(
            f"/api/v1/matches/{MATCH_ID}/classification",
            json={"championship_id": 9999},
        )
        assert response.status_code == 404

        bulk = await client.post(
            "/api/v1/matches/bulk-classify",
            json={"match_ids": [MATCH_ID], "championship_id": 9999},
        )
        assert bulk.status_code == 404


@pytest.mark.asyncio
async def test_update_archive_and_list_filters(db_with_data: None) -> None:
    async with _client() as client:
        championship = await _create_championship(client, "Liga Original")

        updated = await client.patch(
            f"/api/v1/championships/{championship['id']}",
            json={
                "name": "Liga Renomeada",
                "status": "finished",
                "start_date": "2026-01-01",
                "extra": {"organizador": "Liga Local"},
            },
        )
        assert updated.status_code == 200, updated.text
        data = updated.json()
        assert data["name"] == "Liga Renomeada"
        assert data["status"] == "finished"
        assert data["start_date"] == "2026-01-01"
        assert data["extra"] == {"organizador": "Liga Local"}

        edited = await client.post(
            f"/campeonatos/{championship['id']}/editar",
            data={
                "name": "Liga Editada",
                "format": "groups",
                "status": "active",
                "start_date": "2026-02-01",
                "end_date": "",
                "color": "#ef4444",
                "description": "Editada pelo dashboard",
                "external_url": "",
                "extra": '{"sede": "Recife"}',
            },
            follow_redirects=False,
        )
        assert edited.status_code == 303

        detail = await client.get(f"/api/v1/championships/{championship['id']}")
        assert detail.json()["name"] == "Liga Editada"
        assert detail.json()["format"] == "groups"
        assert detail.json()["extra"] == {"sede": "Recife"}

        archived = await client.post(
            f"/campeonatos/{championship['id']}/arquivar",
            follow_redirects=False,
        )
        assert archived.status_code == 303

        hidden = await client.get("/api/v1/championships")
        assert hidden.json()["items"] == []
        visible = await client.get("/api/v1/championships?include_archived=true")
        assert len(visible.json()["items"]) == 1
        assert visible.json()["items"][0]["status"] == "archived"

        unclassified = await client.get(f"/api/v1/matches/{MATCH_ID}/classification")
        assert unclassified.status_code == 200
        assert unclassified.json() is None

        parts = await client.get("/partidas?campeonato=none")
        assert parts.status_code == 200
        assert "Sem campeonato" in parts.text


@pytest.mark.asyncio
async def test_dashboard_pages_and_forms(db_with_data: None) -> None:
    async with _client() as client:
        page = await client.get("/campeonatos")
        assert page.status_code == 200
        assert "Novo campeonato" in page.text

        created = await client.post(
            "/campeonatos",
            data={
                "name": "Copa Interna",
                "format": "knockout",
                "status": "active",
                "color": "#22c55e",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        location = created.headers["location"]
        assert location.startswith("/campeonatos/")

        detail = await client.get(location)
        assert detail.status_code == 200
        assert "Copa Interna" in detail.text
        assert "Mata-mata" in detail.text

        dashboard_match = await client.get(f"/partidas/{MATCH_ID}")
        assert dashboard_match.status_code == 200
        assert "Campeonato" in dashboard_match.text

        parts = await client.get("/partidas")
        assert parts.status_code == 200
        assert "Classificar selecionadas" in parts.text
