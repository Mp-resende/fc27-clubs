from __future__ import annotations

import copy
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db, get_settings
from app.api.main import app
from app.config import settings
from app.core.evidence import align_extraction, normalize_extraction, parse_summary_text
from app.core.review import (
    STATUS_CORRECTED,
    STATUS_IGNORED,
    STATUS_NEEDS_REVIEW,
    STATUS_VALID,
    auto_review_match,
)
from app.db import models  # noqa: F401 – register all ORM models
from app.db.base import Base
from app.db.models import (
    Championship,
    ClubMatch,
    Match,
    MatchClassification,
    MatchEvidence,
)
from app.worker.ingestor import ingest

PARSER_VERSION = "parser-3.0"
CLUB_ID = "682077"
OPPONENT_ID = "999999"


def _payload(
    sample: dict[str, object],
    *,
    match_id: str,
    goals_a: int,
    goals_b: int,
    seconds: int,
    winner_goals: int = 0,
) -> dict[str, object]:
    raw = copy.deepcopy(sample)
    raw["matchId"] = match_id
    clubs = raw["clubs"]  # type: ignore[assignment]
    assert isinstance(clubs, dict)
    clubs[CLUB_ID]["goals"] = str(goals_a)
    clubs[OPPONENT_ID]["goals"] = str(goals_b)
    for cdata in clubs.values():
        cdata["winnerByDnf"] = "0"

    # The aggregate block lives at the top level in real EA payloads but is
    # nested inside each club entry in the sample fixture. Normalise both.
    top_aggregate = raw.get("aggregate")
    top_aggregate = top_aggregate if isinstance(top_aggregate, dict) else {}

    aggregate: dict[str, dict] = {}
    for cid, cdata in clubs.items():
        assert isinstance(cdata, dict)
        for agg in (
            top_aggregate.get(cid),
            cdata.get("aggregate"),
        ):
            if isinstance(agg, dict):
                agg["goals"] = str(winner_goals if cid == CLUB_ID else 0)
                agg["shots"] = "0"
                agg["passesmade"] = "0"
                agg["passattempts"] = "0"
                aggregate[cid] = agg
    raw["aggregate"] = aggregate

    players = raw["players"]  # type: ignore[assignment]
    assert isinstance(players, dict)
    for cid, roster in players.items():
        for player in roster.values():
            player["secondsPlayed"] = str(seconds)
            player["goals"] = str(winner_goals if cid == CLUB_ID else 0)
            player["assists"] = "0"
            player["shots"] = "0"
            player["passesmade"] = "0"
            player["passattempts"] = "0"
            player["goalsconceded"] = "0"
    return raw


class _Client:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._payloads = payloads

    def get_club_matches(
        self, club_id: str, match_type: str, count: int
    ) -> list[dict[str, object]]:
        return self._payloads


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


def _ingest(session_factory, sample, payloads):  # type: ignore[no-untyped-def]
    session: Session = session_factory()
    try:
        ingest(CLUB_ID, session, _Client(payloads), PARSER_VERSION)  # type: ignore[arg-type]
    finally:
        session.close()


@pytest.fixture()
def api_client(session_factory):  # type: ignore[no-untyped-def]
    def _override():  # type: ignore[no-untyped-def]
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    yield
    app.dependency_overrides.clear()


def _http() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_abandoned_match_is_ignored(
    db_session: Session, sample_match_raw: dict
) -> None:
    raw = _payload(
        sample_match_raw,
        match_id="2000000000001",
        goals_a=0,
        goals_b=0,
        seconds=0,
    )
    ingest(CLUB_ID, db_session, _Client([raw]), PARSER_VERSION)  # type: ignore[arg-type]

    match = db_session.get(Match, "2000000000001")
    assert match is not None
    assert match.review_status == STATUS_IGNORED
    club_match = db_session.get(
        ClubMatch, {"club_id": CLUB_ID, "match_id": "2000000000001"}
    )
    assert club_match is not None
    assert club_match.is_ignored == 1


def test_group_stage_3x0_needs_review_and_campaign_excludes_it(
    session_factory, sample_match_raw: dict
) -> None:
    raw = _payload(
        sample_match_raw,
        match_id="2000000000002",
        goals_a=3,
        goals_b=0,
        seconds=5400,
        winner_goals=3,
    )
    _ingest(session_factory, sample_match_raw, [raw])

    session: Session = session_factory()
    try:
        match = session.get(Match, "2000000000002")
        assert match is not None
        assert match.review_status == STATUS_VALID

        championship = Championship(
            name="Copa de Grupos",
            format="groups",
            status="active",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        session.add(championship)
        session.flush()
        session.add(
            MatchClassification(
                match_id="2000000000002",
                championship_id=championship.id,
                stage="Fase de grupos",
                group_name="A",
                updated_at="2026-01-01T00:00:00Z",
            )
        )
        session.flush()
        auto_review_match(session, "2000000000002")
        session.commit()

        assert match.review_status == STATUS_NEEDS_REVIEW
        assert match.review_reason == "forfeit_3x0"
        club_match = session.get(
            ClubMatch, {"club_id": CLUB_ID, "match_id": "2000000000002"}
        )
        assert club_match is not None
        assert club_match.is_ignored == 1
    finally:
        session.close()


@pytest.mark.asyncio
async def test_correct_result_updates_club_match_and_campaign(
    session_factory, sample_match_raw: dict, api_client
) -> None:
    raw = _payload(
        sample_match_raw,
        match_id="2000000000003",
        goals_a=3,
        goals_b=0,
        seconds=5400,
        winner_goals=3,
    )
    _ingest(session_factory, sample_match_raw, [raw])

    async with _http() as client:
        created = await client.post(
            "/api/v1/championships",
            json={"name": "Copa", "format": "groups"},
        )
        championship_id = created.json()["id"]
        classified = await client.post(
            "/api/v1/matches/2000000000003/classification",
            json={
                "championship_id": championship_id,
                "stage": "Fase de grupos",
                "group_name": "A",
            },
        )
        assert classified.status_code == 200, classified.text

        corrected = await client.post(
            "/api/v1/matches/2000000000003/review/correct",
            json={
                "club_a_goals": 1,
                "club_b_goals": 1,
                "note": "Empate real; adversário quitou depois",
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["review"]["status"] == STATUS_CORRECTED

        detail = await client.get(f"/api/v1/championships/{championship_id}")
        campaign = detail.json()["campaign"]
        assert campaign["played"] == 1
        assert campaign["draws"] == 1
        assert campaign["wins"] == 0
        assert campaign["goals_for"] == 1
        assert campaign["goals_against"] == 1

    session: Session = session_factory()
    try:
        match = session.get(Match, "2000000000003")
        assert match is not None
        assert (match.club_a_goals, match.club_b_goals) == (1, 1)
        assert (match.ea_club_a_goals, match.ea_club_b_goals) == (3, 0)
        club_match = session.get(
            ClubMatch, {"club_id": CLUB_ID, "match_id": "2000000000003"}
        )
        assert club_match is not None
        assert (club_match.goals_for, club_match.goals_against) == (1, 1)
        assert club_match.is_draw == 1
        assert club_match.is_ignored == 0
    finally:
        session.close()

    # A new ingest must not overwrite the manual correction.
    _ingest(session_factory, sample_match_raw, [raw])
    session = session_factory()
    try:
        match = session.get(Match, "2000000000003")
        assert match is not None
        assert (match.club_a_goals, match.club_b_goals) == (1, 1)
        club_match = session.get(
            ClubMatch, {"club_id": CLUB_ID, "match_id": "2000000000003"}
        )
        assert club_match is not None
        assert (club_match.goals_for, club_match.goals_against) == (1, 1)
    finally:
        session.close()


@pytest.mark.asyncio
async def test_ignore_and_restore_endpoints(
    session_factory, sample_match_raw: dict, api_client
) -> None:
    raw = _payload(
        sample_match_raw,
        match_id="2000000000004",
        goals_a=2,
        goals_b=1,
        seconds=5400,
        winner_goals=2,
    )
    _ingest(session_factory, sample_match_raw, [raw])

    async with _http() as client:
        ignored = await client.post(
            "/api/v1/matches/2000000000004/review/ignore",
            json={"note": "Partida cancelada"},
        )
        assert ignored.status_code == 200
        assert ignored.json()["review"]["status"] == STATUS_IGNORED

        queue = await client.get("/api/v1/review/queue")
        assert queue.status_code == 200
        assert any(
            item["match_id"] == "2000000000004"
            for item in queue.json()["items"]
        )

        restored = await client.post(
            "/api/v1/matches/2000000000004/review/restore"
        )
        assert restored.status_code == 200
        assert restored.json()["review"]["status"] == STATUS_VALID


@pytest.mark.asyncio
async def test_evidence_upload_returns_extraction_fallback(
    session_factory, sample_match_raw: dict, api_client, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "VISION_API_KEY", "")

    raw = _payload(
        sample_match_raw,
        match_id="2000000000005",
        goals_a=1,
        goals_b=1,
        seconds=5400,
        winner_goals=1,
    )
    _ingest(session_factory, sample_match_raw, [raw])

    async with _http() as client:
        response = await client.post(
            "/api/v1/matches/2000000000005/review/evidence",
            files={"file": ("sumula.png", b"fake-image-bytes", "image/png")},
            data={"apply": "false"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evidence_id"] > 0
    assert body["extractor"] in ("none", "ocr", "vision")

    session: Session = session_factory()
    try:
        evidence = session.query(MatchEvidence).one()
        assert evidence.original_name == "sumula.png"
    finally:
        session.close()


def test_parse_summary_text_extracts_score() -> None:
    data = parse_summary_text(
        "PLACAR FINAL\nMVL ES 2 - 1 Adversario\nGol: Striker 32'"
    )
    assert data["club_a_goals"] == 2
    assert data["club_b_goals"] == 1


def test_parse_summary_text_ignores_dates() -> None:
    data = parse_summary_text("Data: 2026-11-01\nPlacar: 1 x 0")
    assert data["club_a_goals"] == 1
    assert data["club_b_goals"] == 0


def test_normalize_and_align_extraction() -> None:
    normalized = normalize_extraction(
        {"home_team": "Rival FC", "away_team": "MVL ES", "home_goals": 2, "away_goals": 2}
    )
    assert normalized["club_a_goals"] == 2
    assert normalized["club_b_goals"] == 2

    aligned, warnings = align_extraction(dict(normalized), "MVL ES", "Rival FC")
    assert aligned["club_a_goals"] == 2
    assert aligned["club_b_goals"] == 2
    assert aligned["swapped"] is True
    assert warnings


@pytest.mark.asyncio
async def test_scan_matches_api_counts_changes(
    session_factory, sample_match_raw: dict, api_client
) -> None:
    abandoned = _payload(
        sample_match_raw,
        match_id="2000000000006",
        goals_a=0,
        goals_b=0,
        seconds=0,
    )
    _ingest(session_factory, sample_match_raw, [abandoned])

    async with _http() as client:
        response = await client.post("/api/v1/review/scan")
    assert response.status_code == 200
    assert response.json()["scanned"] >= 1
