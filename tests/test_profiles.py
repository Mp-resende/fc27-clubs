"""Tests for the EA season snapshot parsers and the member/player link."""
from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.core.member_link import (
    is_usable_name,
    link_member,
    link_members,
    normalize_name,
)
from app.core.parser import parse_member_career, parse_member_stats, parse_overall_stats
from app.db.models import ClubMember, Player, PlayerMatchStats, PlayerName


def _seed_player(db: Session, player_key: str = "k1", name: str = "Dougg Rooney") -> None:
    now = "2026-01-01T00:00:00Z"
    db.add(
        Player(
            player_key=player_key,
            platform="common-gen5",
            ea_player_id=None,
            first_seen_at=now,
            updated_at=now,
        )
    )
    db.add(
        PlayerName(
            player_key=player_key,
            playername=name,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db.add(
        PlayerMatchStats(
            match_id="m1",
            player_key=player_key,
            club_id="682077",
            playername=name,
            position="forward",
            seconds_played=900,
            raw_player="{}",
        )
    )
    db.flush()


def _member(db: Session, name: str) -> None:
    db.add(
        ClubMember(
            club_id="682077",
            platform="common-gen5",
            name=name,
            raw_json="{}",
            updated_at="2026-01-01T00:00:00Z",
        )
    )
    db.flush()


# --- Name normalisation ------------------------------------------------------


def test_normalize_name_variants() -> None:
    assert normalize_name("Dougg Rooney") == "dougg rooney"
    assert normalize_name("dougg-rooney") == "dougg rooney"
    assert normalize_name("Dougg   Rooney") == "dougg rooney"
    assert normalize_name("-") == ""
    assert normalize_name(None) == ""


def test_is_usable_name_rejects_placeholders() -> None:
    assert not is_usable_name("-")
    assert is_usable_name("vitor14vr")


# --- Parsers -----------------------------------------------------------------


def test_parse_member_stats_shape() -> None:
    payload = {
        "members": [
            {
                "name": "Dougg Rooney",
                "gamesPlayed": "27",
                "goals": "20",
                "assists": "17",
                "passSuccessRate": "83",
                "ratingAve": "7.4",
                "proOverall": "80",
                "proPos": "25",
                "favoritePosition": "forward",
                "prevGoals": "2",
                "prevGoals1": "1",
                "prevGoals5": "3",
            }
        ],
        "positionCount": {},
    }
    rows = parse_member_stats("682077", payload, "common-gen5")
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Dougg Rooney"
    assert row["pro_pos"] == 25
    assert row["rating_avg"] == pytest.approx(7.4)
    prev = json.loads(row["prev_goals"])
    assert prev[0] == 2
    assert prev[1] == 1
    assert prev[5] == 3


def test_parse_member_stats_skips_blank_names() -> None:
    rows = parse_member_stats(
        "682077", {"members": [{"name": "-"}], "positionCount": {}}, "common-gen5"
    )
    assert rows == []


def test_parse_member_career_shape() -> None:
    payload = {
        "members": [
            {
                "name": "MathI12_GK",
                "gamesPlayed": "100",
                "goals": "4",
                "assists": "9",
                "manOfTheMatch": "12",
                "ratingAve": "6.9",
            }
        ]
    }
    rows = parse_member_career("682077", payload, "common-gen5")
    assert rows[0]["man_of_the_match"] == 12
    assert rows[0]["rating_avg"] == pytest.approx(6.9)


def test_parse_overall_stats_basic() -> None:
    raw = {
        "gamesPlayed": "123",
        "wins": "62",
        "ties": "9",
        "losses": "52",
        "goals": "371",
        "goalsAgainst": "323",
        "skillRating": "1794",
        "reputationtier": "1",
    }
    parsed = parse_overall_stats("682077", raw, "common-gen5")
    assert parsed["games_played"] == 123
    assert parsed["skill_rating"] == 1794
    assert parsed["reputation_tier"] == 1
    assert parsed["platform"] == "common-gen5"


# --- Linking -----------------------------------------------------------------


def test_link_members_auto_by_name(db_session: Session) -> None:
    _seed_player(db_session)
    _member(db_session, "Dougg Rooney")

    result = link_members(db_session, "682077", "common-gen5")
    assert result["linked"] == 1
    member = db_session.get(
        ClubMember,
        {"club_id": "682077", "platform": "common-gen5", "name": "Dougg Rooney"},
    )
    assert member.player_key == "k1"


def test_link_members_respects_alias(db_session: Session) -> None:
    _seed_player(db_session, player_key="k1", name="Dougg Rooney")
    _member(db_session, "D. Rooney")  # EA name differs from the in-match name

    link_member(db_session, "682077", "common-gen5", "D. Rooney", "k1")
    db_session.flush()

    result = link_members(db_session, "682077", "common-gen5")
    assert result["linked"] == 1
    member = db_session.get(
        ClubMember,
        {"club_id": "682077", "platform": "common-gen5", "name": "D. Rooney"},
    )
    assert member.player_key == "k1"


def test_link_members_unmatched_stays_unresolved(db_session: Session) -> None:
    _seed_player(db_session)
    _member(db_session, "Totalmente Outro")
    result = link_members(db_session, "682077", "common-gen5")
    assert result["unresolved"] == 1
    member = db_session.get(
        ClubMember,
        {"club_id": "682077", "platform": "common-gen5", "name": "Totalmente Outro"},
    )
    assert member.player_key is None


def test_link_member_persists_manual_alias(db_session: Session) -> None:
    _seed_player(db_session)
    link_member(db_session, "682077", "common-gen5", "Some Name", "k1")
    db_session.flush()
    _member(db_session, "Some Name")

    link_members(db_session, "682077", "common-gen5")
    member = db_session.get(
        ClubMember,
        {"club_id": "682077", "platform": "common-gen5", "name": "Some Name"},
    )
    assert member.player_key == "k1"


def test_link_member_rejects_empty_name(db_session: Session) -> None:
    with pytest.raises(ValueError):
        link_member(db_session, "682077", "common-gen5", "   ", "k1")
