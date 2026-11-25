"""Testes das funcionalidades inspiradas no SpotXI: Telemetria, Perfil de Atleta e Busca."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.main import app
from app.core.player_analytics import calculate_performance_score
from app.db.base import Base
from app.db.models import Club, Match, Player, PlayerMatchStats, PlayerName


def test_calculate_performance_score_forward():
    # Atacante com 30 jogos, 25 gols, 10 assistências, rating 7.8
    stats = {
        "goals": 25,
        "assists": 10,
        "tackles_made": 5,
        "saves": 0,
        "avg_rating": 7.8,
    }
    score = calculate_performance_score("ST", stats, matches=30)
    assert 70.0 <= score <= 99.9


def test_calculate_performance_score_midfielder():
    # Médio com 25 jogos, 5 gols, 20 assistências, 40 desarmes, rating 7.5
    stats = {
        "goals": 5,
        "assists": 20,
        "tackles_made": 40,
        "saves": 0,
        "avg_rating": 7.5,
    }
    score = calculate_performance_score("CAM", stats, matches=25)
    assert 65.0 <= score <= 99.9


def test_calculate_performance_score_unqualified():
    # Atleta com menos de 20 jogos recebe penalidade de confiança
    stats = {"goals": 10, "assists": 5, "avg_rating": 9.0}
    score_5_games = calculate_performance_score("ST", stats, matches=5)
    score_25_games = calculate_performance_score("ST", stats, matches=25)
    assert score_5_games < score_25_games


def test_suggest_and_player_profile_endpoints():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()

    # Criação de massa de dados de teste
    player = Player(
        player_key="p_neymar_test",
        ea_player_id="1979979809",
        platform="common-gen5",
        first_seen_at="2026-11-01T00:00:00Z",
        updated_at="2026-11-13T00:00:00Z",
    )
    session.add(player)

    name1 = PlayerName(
        player_key="p_neymar_test",
        playername="Neymar_11BL",
        first_seen_at="2026-11-01T00:00:00Z",
        last_seen_at="2026-11-13T00:00:00Z",
    )
    name2 = PlayerName(
        player_key="p_neymar_test",
        playername="NeymarAntigo_10",
        first_seen_at="2026-10-01T00:00:00Z",
        last_seen_at="2026-10-31T00:00:00Z",
    )
    session.add_all([name1, name2])

    club = Club(
        club_id="club_spotxi_1",
        platform="common-gen5",
        name="TENKA FC",
        first_seen_at="2026-11-01T00:00:00Z",
        updated_at="2026-11-13T00:00:00Z",
    )
    session.add(club)

    match = Match(
        match_id="m_test_spotxi_1",
        platform="common-gen5",
        match_type_code="friendly",
        played_at="2026-11-13T15:00:00Z",
        club_a_id="club_spotxi_1",
        club_b_id="club_rival_1",
        club_a_goals=3,
        club_b_goals=1,
        raw_payload="{}",
        payload_hash="hash1",
        parser_version="1",
        ingested_at="2026-11-13T15:30:00Z",
        first_seen_at="2026-11-13T15:30:00Z",
        last_seen_at="2026-11-13T15:30:00Z",
        review_status="valid",
    )
    session.add(match)

    pms = PlayerMatchStats(
        match_id="m_test_spotxi_1",
        club_id="club_spotxi_1",
        player_key="p_neymar_test",
        playername="Neymar_11BL",
        position="CAM",
        rating=8.2,
        seconds_played=5400,
        goals=2,
        assists=1,
        shots=4,
        passes_made=35,
        pass_attempts=40,
        tackles_made=3,
        tackle_attempts=4,
        raw_player="{}",
    )
    session.add(pms)
    session.commit()
    session.close()

    def _get_db_override():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db_override
    try:
        client = TestClient(app)

        # 1. Testar Busca por nome atual
        resp_search = client.get("/api/v1/search/suggest?q=Neymar")
        assert resp_search.status_code == 200
        results = resp_search.json()
        assert len(results) >= 1
        assert any(r["label"] == "Neymar_11BL" for r in results)

        # 2. Testar Busca por nome antigo
        resp_search_old = client.get("/api/v1/search/suggest?q=NeymarAntigo")
        assert resp_search_old.status_code == 200
        results_old = resp_search_old.json()
        assert any(r["id"] == "p_neymar_test" for r in results_old)

        # 3. Testar Ficha Completa do Jogador
        resp_profile = client.get("/api/v1/players/p_neymar_test/profile")
        assert resp_profile.status_code == 200
        data = resp_profile.json()
        assert data["player_key"] == "p_neymar_test"
        assert data["primary_position"] == "CAM"
        assert data["summary"]["total_goals"] == 2
        assert data["summary"]["total_assists"] == 1
        assert data["telemetry"]["passing"]["pass_accuracy_pct"] == 87.5
        assert data["telemetry"]["defending"]["tackle_success_pct"] == 75.0
        assert len(data["recent_form"]) == 1
        assert data["recent_form"][0]["club_name"] == "TENKA FC"

        # 4. Testar Equipe da Semana (TOTW)
        resp_totw = client.get("/api/v1/analytics/totw?days=30")
        assert resp_totw.status_code == 200
        totw_data = resp_totw.json()
        assert totw_data["formation"] == "3-1-4-2"
        assert totw_data["player_of_the_week"] is not None
        assert totw_data["player_of_the_week"]["player_key"] == "p_neymar_test"

        # 5. Testar Detector de Panelinhas (Lineups)
        resp_lineups = client.get("/api/v1/clubs/club_spotxi_1/lineups?min_matches=1")
        assert resp_lineups.status_code == 200
        assert resp_lineups.json()["club_id"] == "club_spotxi_1"
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
