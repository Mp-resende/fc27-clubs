from __future__ import annotations

from app.core.decoder import decode_events, parse_event_string


def test_parse_event_string_basic() -> None:
    result = parse_event_string("24:12,30:6")
    assert result == {24: 12, 30: 6}


def test_parse_event_string_empty() -> None:
    assert parse_event_string("") == {}
    assert parse_event_string("   ") == {}


def test_parse_event_string_single() -> None:
    assert parse_event_string("215:45") == {215: 45}


def test_decode_four_buckets() -> None:
    """Events from all 4 buckets should be aggregated."""
    player: dict[str, object] = {
        "match_event_aggregate_0": "215:10",
        "match_event_aggregate_1": "215:5",
        "match_event_aggregate_2": "215:3",
        "match_event_aggregate_3": "215:2",
    }
    raw_events, named = decode_events(player)
    assert raw_events[215] == 20
    assert named.get("passes_completed") == 20


def test_fouls_derived() -> None:
    """fouls_conceded = event_2 + event_3."""
    player: dict[str, object] = {
        "match_event_aggregate_0": "2:4,3:2",
        "match_event_aggregate_1": "",
        "match_event_aggregate_2": "",
        "match_event_aggregate_3": "",
    }
    _, named = decode_events(player)
    assert named["fouls_conceded"] == 6


def test_yellow_cards_derived() -> None:
    """yellow_cards = event_95 + event_213."""
    player: dict[str, object] = {
        "match_event_aggregate_0": "95:1",
        "match_event_aggregate_1": "213:1",
        "match_event_aggregate_2": "",
        "match_event_aggregate_3": "",
    }
    _, named = decode_events(player)
    assert named["yellow_cards"] == 2
    assert named.get("yellow_cards_immediate") == 1
    assert named.get("yellow_cards_delayed") == 1


def test_passes_completed() -> None:
    """Event 215 maps to passes_completed."""
    player: dict[str, object] = {
        "match_event_aggregate_0": "215:45",
        "match_event_aggregate_1": "",
        "match_event_aggregate_2": "",
        "match_event_aggregate_3": "",
    }
    _, named = decode_events(player)
    assert named["passes_completed"] == 45


def test_empty_aggregate_no_error() -> None:
    """All empty buckets must not raise."""
    player: dict[str, object] = {
        "match_event_aggregate_0": "",
        "match_event_aggregate_1": "",
        "match_event_aggregate_2": "",
        "match_event_aggregate_3": "",
    }
    raw_events, named = decode_events(player)
    assert raw_events == {}
    assert named == {}


def test_missing_buckets_no_error() -> None:
    """Player dict with no event keys must not raise."""
    raw_events, named = decode_events({})
    assert raw_events == {}
    assert named == {}
