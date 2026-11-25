from __future__ import annotations

from app.core.parser import parse_match, parse_player, sha256_hex


PARSER_VERSION = "parser-3.0"


def _make_match() -> dict[str, object]:
    # Real EA shape: match-level metadata lives inside each clubs[cid] entry.
    return {
        "matchId": "1000000000001",
        "timestamp": 1767297600,
        "clubs": {
            "682077": {
                "goals": "3",
                "winnerByDnf": "0",
                "matchType": "friendlyMatch",
                "season_id": "27",
                "gameNumber": "42",
            },
            "999999": {
                "goals": "1",
                "winnerByDnf": "0",
                "matchType": "friendlyMatch",
                "season_id": "27",
                "gameNumber": "42",
            },
        },
        "players": {},
    }


def test_parse_match_fields() -> None:
    raw = _make_match()
    result = parse_match(raw, PARSER_VERSION)

    assert result["match_id"] == "1000000000001"
    assert result["match_type_code"] == "friendlyMatch"
    assert result["season_id"] == "27"
    assert result["game_number"] == "42"
    assert result["parser_version"] == PARSER_VERSION
    assert result["dnf"] == 0
    assert result["winner_by_dnf"] is None


def test_parse_match_top_level_fallback() -> None:
    """Older/simplified payloads keep matchType at the top level."""
    raw: dict[str, object] = {
        "matchId": "1000000000002",
        "timestamp": 1767297600,
        "matchType": "friendlyMatch",
        "seasonId": "27",
        "gameNumber": "7",
        "clubs": {
            "682077": {"goals": "0", "winnerByDnf": "0"},
            "999999": {"goals": "0", "winnerByDnf": "0"},
        },
        "players": {},
    }
    result = parse_match(raw, PARSER_VERSION)
    assert result["match_type_code"] == "friendlyMatch"
    assert result["season_id"] == "27"
    assert result["game_number"] == "7"


def test_canonical_club_order() -> None:
    """Club with the numerically smaller ID is always club_a."""
    raw = _make_match()
    result = parse_match(raw, PARSER_VERSION)
    # 682077 < 999999
    assert result["club_a_id"] == "682077"
    assert result["club_b_id"] == "999999"
    assert result["club_a_goals"] == 3
    assert result["club_b_goals"] == 1


def test_parse_timestamp() -> None:
    raw = _make_match()
    result = parse_match(raw, PARSER_VERSION)
    # 1767297600 → 2026-01-01T08:00:00Z (UTC)
    played_at = str(result["played_at"])
    assert played_at.endswith("Z")
    assert "T" in played_at


def test_payload_hash_stable() -> None:
    raw = _make_match()
    r1 = parse_match(raw, PARSER_VERSION)
    r2 = parse_match(raw, PARSER_VERSION)
    assert r1["payload_hash"] == r2["payload_hash"]
    assert len(str(r1["payload_hash"])) == 64  # SHA-256 hex


def test_parse_player_rating_null_when_zero_seconds() -> None:
    player_data: dict[str, object] = {
        "playername": "SubPlayer",
        "pos": "LW",
        "archetypeid": "13",
        "rating": "0",
        "secondsPlayed": "0",
        "gameTime": "0",
        "goals": "0",
        "assists": "0",
        "shots": "0",
        "passesmade": "0",
        "passattempts": "0",
        "tacklesmade": "0",
        "tackleattempts": "0",
        "saves": "0",
        "goalsconceded": "0",
        "cleansheetsany": "0",
        "cleansheetsdef": "0",
        "cleansheetsgk": "0",
        "mom": "0",
        "redcards": "0",
        "match_event_aggregate_0": "",
        "match_event_aggregate_1": "",
        "match_event_aggregate_2": "",
        "match_event_aggregate_3": "",
    }
    result = parse_player("player_key_x", "682077", "match_1", player_data)
    assert result["rating"] is None, "rating must be None when secondsPlayed == 0, not 0.0"


def test_sha256_hex_length() -> None:
    assert len(sha256_hex("hello")) == 64
