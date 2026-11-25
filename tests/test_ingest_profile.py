"""Tests for multi-type match fetch, dedupe by matchId and profile sync."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import (
    Club,
    ClubMember,
    ClubMemberCareer,
    ClubOverallStats,
    Match,
)
from app.worker.ingestor import ingest, sync_club_profile

PARSER_VERSION = "parser-3.0"
CLUB_ID = "682077"


def test_fetch_covers_all_match_types_and_dedupes(
    db_session: Session, sample_match_raw: dict
) -> None:
    class _Client:
        calls: list[str] = []

        def get_club_matches(self, club_id: str, match_type: str, count: int):
            self.calls.append(match_type)
            return [sample_match_raw]

    client = _Client()
    result = ingest(CLUB_ID, db_session, client, PARSER_VERSION)  # type: ignore[arg-type]
    assert client.calls == ["leagueMatch", "friendlyMatch", "playoffMatch"]
    # The same payload from three endpoints must produce a single stored match.
    assert result.matches_returned == 1
    assert db_session.query(Match).count() == 1


OVERALL_ROWS = [
    {
        "clubId": CLUB_ID,
        "gamesPlayed": "10",
        "wins": "6",
        "ties": "1",
        "losses": "3",
        "goals": "20",
        "goalsAgainst": "12",
        "skillRating": "1794",
        "lastMatch0": "1",
        "lastOpponent0": "100",
    }
]
MEMBER_ROWS = [
    {
        "name": "Dougg Rooney",
        "gamesPlayed": "27",
        "goals": "20",
        "assists": "17",
        "ratingAve": "7.4",
    },
    {"name": "-"},  # placeholder must be skipped
]
CAREER_ROWS = [
    {"name": "Dougg Rooney", "gamesPlayed": "5", "goals": "4"},
]


class _ProfileClient:
    def get_club_overall_stats(self, club_id: str):
        return OVERALL_ROWS

    def get_member_stats(self, club_id: str):
        return {"members": MEMBER_ROWS, "positionCount": {}}

    def get_member_career_stats(self, club_id: str):
        return {"members": CAREER_ROWS}


def test_sync_club_profile_upserts_snapshot(db_session: Session) -> None:
    db_session.add(
        Club(
            club_id=CLUB_ID,
            platform="common-gen5",
            name="MVL ES",
            first_seen_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
    )
    db_session.flush()

    summary = sync_club_profile(
        CLUB_ID, db_session, _ProfileClient(), "common-gen5"
    )
    assert summary["overall"] is True
    assert summary["members"] == 1  # "-" dropped
    assert summary["career"] == 1
    assert db_session.query(ClubOverallStats).count() == 1
    member = db_session.query(ClubMember).one()
    assert member.name == "Dougg Rooney"
    assert member.overall is None  # proOverall not in this fixture row

    # Second run updates in place instead of duplicating.
    OVERALL_ROWS[0]["gamesPlayed"] = "11"
    summary2 = sync_club_profile(CLUB_ID, db_session, _ProfileClient(), "common-gen5")
    assert summary2["overall"] is True
    assert db_session.query(ClubOverallStats).count() == 1
    overall = db_session.query(ClubOverallStats).one()
    assert overall.games_played == 11
    assert db_session.query(ClubMemberCareer).count() == 1

    last_results = json.loads(overall.last_results)
    assert last_results == [{"result": 1, "opponent": "100"}]


def test_ingest_profile_error_does_not_block_matches(
    db_session: Session, sample_match_raw: dict
) -> None:
    class _Client:
        def get_club_matches(self, club_id: str, match_type: str, count: int):
            return [sample_match_raw]

        def get_club_overall_stats(self, club_id: str):
            raise RuntimeError("ea down")

    result = ingest(CLUB_ID, db_session, _Client(), PARSER_VERSION)  # type: ignore[arg-type]
    assert result.matches_inserted == 1
    assert result.profile_synced is False
    assert result.profile_error is not None
    # The run itself still closes with success for the match part.
    assert db_session.query(Match).count() == 1
