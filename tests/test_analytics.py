"""Tests for the analytics layer: filters, summaries and derived metrics."""
from __future__ import annotations

import json

import pytest

from app.core import analytics
from app.core.analytics import (
    MatchFilters,
    moving_average,
    official_summary,
    parse_json_list,
    per90,
    position_group,
    safe_div,
)
from app.core.parser import parse_overall_stats
from app.db.models import ClubMatch, Match


def _match(match_id: str, played_at: str, code: str = "5") -> Match:
    return Match(
        match_id=match_id,
        club_a_id="682077",
        club_b_id="100",
        played_at=played_at,
        review_status="valid",
        match_type_code=code,
    )


def _club_match(match_id: str, gf: int, ga: int) -> ClubMatch:
    return ClubMatch(
        club_id="682077",
        match_id=match_id,
        goals_for=gf,
        goals_against=ga,
        is_win=1 if gf > ga else 0,
        is_draw=1 if gf == ga else 0,
        is_loss=1 if gf < ga else 0,
        is_ignored=0,
    )


# ─── Pure helpers ────────────────────────────────────────────────────────────


def test_per90() -> None:
    assert per90(2, 5400) == pytest.approx(2.0, abs=0.01)
    assert per90(1, 0) == 0.0


def test_safe_div() -> None:
    assert safe_div(1, 0) == 0.0
    assert safe_div(1, 2) == 0.5


def test_moving_average_window() -> None:
    out = moving_average([1, 2, 3, 4, 5], 3)
    assert out[0] == 1
    assert out[1] == pytest.approx(1.5)
    assert out[2] == 2.0
    assert out[4] == 4.0


def test_position_group() -> None:
    assert position_group("goalkeeper") == "GK"
    assert position_group("defender") == "DEF"
    assert position_group("midfielder") == "MID"
    assert position_group("forward") == "FWD"
    assert position_group(None, 25) == "FWD"
    assert position_group(None, None) == "UNK"


def test_match_type_labels() -> None:
    from app.core.analytics import match_type_key, normalize_match_type

    assert normalize_match_type("1") == "league"
    assert normalize_match_type("5") == "friendly"
    assert normalize_match_type("leagueMatch") == "league"
    assert normalize_match_type("friendlyMatch") == "friendly"
    assert normalize_match_type("playoffMatch") == "playoff"
    assert normalize_match_type("weird") is None
    assert match_type_key("5") == "friendly"


# ─── MatchFilters ────────────────────────────────────────────────────────────


def test_filters_from_query_valid_and_invalid() -> None:
    filters = MatchFilters.from_query({"tipo": "league", "resultado": "win", "n": "5"})
    assert filters.match_type == "league"
    assert filters.result == "win"
    assert filters.last_n == 5

    junk = MatchFilters.from_query({"tipo": "banana", "resultado": "x", "n": "-2"})
    assert junk.match_type is None
    assert junk.result is None
    assert junk.last_n is None
    assert not junk.is_active

    active = MatchFilters.from_query({"tipo": "friendly"})
    assert active.is_active
    assert active.as_query() == {"tipo": "friendly"}


def test_filters_describe() -> None:
    filters = MatchFilters.from_query({"tipo": "league", "dias": "7"})
    desc = filters.describe()
    assert any("Liga" in part for part in desc)
    assert any("dias" in part for part in desc)


def test_filters_margin_and_virtual_fields() -> None:
    filters = MatchFilters.from_query({"margem": "3+", "adversario": "100"})
    assert filters.margin == "3+"
    assert filters.opponent == "100"
    assert "3" in filters.describe()[1] or any("3+" in part for part in filters.describe())


# ─── local_summary ───────────────────────────────────────────────────────────


def test_local_summary_counts_and_form() -> None:
    rows = [
        (_club_match("m1", 2, 1), _match("m1", "2026-01-02T10:00:00Z")),
        (_club_match("m2", 1, 1), _match("m2", "2026-01-01T10:00:00Z")),
        (_club_match("m3", 0, 3), _match("m3", "2025-12-31T10:00:00Z")),
    ]
    summary = analytics.local_summary(rows)
    assert summary["played"] == 3
    assert summary["wins"] == 1
    assert summary["draws"] == 1
    assert summary["losses"] == 1
    assert summary["points"] == 4
    assert summary["goals_for"] == 3
    assert summary["goals_against"] == 5
    assert summary["goal_diff"] == -2
    assert summary["form"] == ["L", "D", "W"]
    assert summary["sessions"] == 3
    assert summary["streak"]["letter"] == "W"
    assert summary["ppg"] == pytest.approx(round(4 / 3, 2))


def test_local_summary_empty() -> None:
    summary = analytics.local_summary([])
    assert summary["played"] == 0
    assert summary["ppg"] == 0.0


def test_h2h_summary_filters_by_opponent() -> None:
    rows = [
        (_club_match("m1", 2, 1), _match("m1", "2026-01-02T10:00:00Z")),
        (_club_match("m2", 0, 1), _match("m2", "2026-01-01T10:00:00Z", code="1")),
    ]
    summary = analytics.h2h_summary(rows, "682077", "100")
    assert summary["played"] == 2
    assert summary["opponent_id"] == "100"
    assert len(summary["meetings"]) == 2
    other = analytics.h2h_summary(rows, "682077", "999")
    assert other["played"] == 0


# ─── official_summary (EA season snapshot) ──────────────────────────────────


def _fake_overall(parsed: dict[str, object]) -> object:
    class _Row:
        pass

    row = _Row()
    for key, value in parsed.items():
        setattr(row, key, value)
    return row


def test_parse_overall_stats_last_results_codes() -> None:
    raw = {
        "clubId": "682077",
        "gamesPlayed": "10",
        "wins": "5",
        "ties": "2",
        "losses": "3",
        "goals": "15",
        "goalsAgainst": "10",
        "lastMatch0": "1",
        "lastOpponent0": "1569491",
        "lastMatch1": "3",
        "lastOpponent1": "224806",
        "lastMatch2": "-1",
        "lastMatch3": "2",
        "lastOpponent3": "632947",
        "finishesInDivision31": "2",
    }
    parsed = parse_overall_stats("682077", raw, "common-gen5")
    assert json.loads(parsed["last_results"]) == [
        {"result": 1, "opponent": "1569491"},
        {"result": 3, "opponent": "224806"},
        {"result": 2, "opponent": "632947"},
    ]
    assert json.loads(parsed["division_finishes"]) == [
        {"division": 3, "group": 1, "count": 2}
    ]

    assert official_summary(None) is None

    summary = official_summary(_fake_overall(parsed), {"1569491": "Familia CR"})
    assert summary["games_played"] == 10
    assert summary["ppg"] == pytest.approx(1.7, abs=0.05)
    assert summary["win_rate"] == pytest.approx(50.0, abs=0.5)
    assert summary["form_newest"] == ["W", "D", "L"]
    assert summary["last_opponents"][0]["opponent_name"] == "Familia CR"
    assert summary["streak"]["letter"] == "W"


def test_goals_chart_uses_reverse_chronology() -> None:
    rows = [
        (_club_match("m1", 2, 0), _match("m1", "2026-01-03T10:00:00Z")),
        (_club_match("m2", 1, 3), _match("m2", "2026-01-02T10:00:00Z")),
    ]
    names = {"100": "Rival"}
    chart = analytics.goals_chart(rows, "682077", names)
    assert chart["labels"] == ["Rival", "Rival"]  # oldest first
    assert chart["goals_for"] == [1, 2]
    assert chart["goals_against"] == [3, 0]


def test_recent_matches_fields() -> None:
    rows = [
        (_club_match("m1", 2, 0), _match("m1", "2026-01-02T10:00:00Z", code="1")),
    ]
    view = analytics.recent_matches(rows, {"100": "Rival"}, "682077")
    item = view[0]
    assert item["opponent_name"] == "Rival"
    assert item["letter"] == "W"
    assert item["type_label"] == "Liga"
    assert item["type_short"] == "LIG"


def test_parse_json_list_tolerates_garbage() -> None:
    assert parse_json_list(None) == []
    assert parse_json_list("") == []
    assert parse_json_list("not json") == []
    assert parse_json_list('{"a": 1}') == []  # dict, not list
    assert parse_json_list("[1, 2]") == [1, 2]
