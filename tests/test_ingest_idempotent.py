from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db.models import ClubMatch, Match, MatchPayloadArchive, Player, PlayerMatchStats
from app.worker.ingestor import ingest


PARSER_VERSION = "parser-3.0"
CLUB_ID = "682077"


def test_insert_once(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    result = ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    assert result.matches_inserted == 1
    assert result.matches_updated == 0
    assert db_session.query(Match).count() == 1


def test_insert_twice_no_duplicate(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    assert db_session.query(Match).count() == 1


def test_seen_count_increments(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    match = db_session.query(Match).first()
    assert match is not None
    assert match.seen_count == 2


def test_both_clubs_in_club_matches(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    club_matches = db_session.query(ClubMatch).all()
    club_ids = {cm.club_id for cm in club_matches}
    assert "682077" in club_ids
    assert "999999" in club_ids


def test_player_stats_created(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    count = db_session.query(PlayerMatchStats).count()
    assert count == 10  # 5 players × 2 clubs


def test_players_table_populated(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    count = db_session.query(Player).count()
    assert count == 10


def test_sub_player_rating_is_null(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    sub = db_session.query(PlayerMatchStats).filter(PlayerMatchStats.player_key == "player_a5").first()
    assert sub is not None
    assert sub.rating is None


def test_payload_archive_created(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    assert db_session.query(MatchPayloadArchive).count() == 1


def test_payload_archive_is_idempotent(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    assert db_session.query(MatchPayloadArchive).count() == 1


def test_payload_archive_ignores_volatile_time_ago(
    db_session: Session, sample_match_raw: dict, ea_client_mock: object
) -> None:
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    sample_match_raw["timeAgo"] = "agora mesmo"
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    assert db_session.query(MatchPayloadArchive).count() == 1


def test_history_is_never_pruned(db_session: Session, sample_match_raw: dict, ea_client_mock: object) -> None:
    """Old matches stay after new ingestion cycles (append-only history)."""
    ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    first_id = db_session.query(Match).one().match_id
    for _ in range(3):
        ingest(CLUB_ID, db_session, ea_client_mock, PARSER_VERSION)  # type: ignore[arg-type]
    assert db_session.query(Match).filter(Match.match_id == first_id).count() == 1
