from __future__ import annotations

EVENT_MAP: dict[int, str] = {
    0: "tackles_won",
    1: "tackles_lost",
    2: "fouls_conceded",
    3: "fouls_conceded_defensive_third",
    4: "fouls_won",
    6: "interceptions",
    10: "corners_conceded",
    11: "second_assists",
    13: "shots_on_target_inside_box",
    14: "shots_off_target_inside_box",
    18: "shots_on_target_outside_box",
    19: "shots_off_target_outside_box",
    24: "passes_completed_short",
    25: "passes_failed_short",
    26: "passes_completed_medium",
    27: "passes_failed_medium",
    28: "passes_completed_long",
    29: "passes_failed_long",
    30: "passes_completed_forward",
    31: "passes_failed_forward",
    32: "passes_completed_backward",
    33: "passes_failed_backward",
    34: "passes_completed_sideways",
    35: "passes_failed_sideways",
    36: "passes_completed_cross",
    37: "passes_failed_cross",
    38: "dribbles_skill_move_beat",
    94: "penalties_conceded",
    95: "yellow_cards_immediate",
    97: "dribbles_carried",
    99: "oop_severity_1",
    100: "oop_severity_2",
    101: "oop_severity_3",
    102: "oop_severity_4",
    103: "oop_severity_5",
    105: "possession_lost_defensive_third",
    106: "possession_lost_middle_third",
    107: "possession_lost_attacking_third",
    108: "possession_won_defensive_third",
    109: "possession_won_middle_third",
    110: "possession_won_attacking_third",
    111: "in_position",
    112: "dribbles_beat",
    115: "second_assists",
    118: "through_ball_assists",
    128: "goals_first_time",
    131: "goals_weak_foot",
    136: "goals_off_post",
    143: "first_time_passes",
    144: "switches_of_play",
    145: "corners_taken",
    147: "flair_passes",
    152: "through_passes",
    153: "offside_passes",
    156: "crosses_blocked",
    158: "opponents_dispossessed",
    163: "dangerous_tackles",
    164: "clean_tackles",
    174: "dribbles_completed",
    175: "feedback_no_good_option",
    176: "feedback_good_option_taken",
    177: "feedback_best_option_taken",
    182: "feedback_should_have_passed_elsewhere",
    183: "feedback_chose_to_pass",
    202: "shots_saved",
    207: "feedback_pick_your_pass",
    212: "feedback_use_the_ball",
    213: "yellow_cards_delayed",
    214: "goals_by_events",
    215: "passes_completed",
    216: "passes_failed",
    217: "shots_on_target",
    218: "shots_off_target",
    219: "out_of_position",
    229: "standing_tackles_won",
    230: "sliding_tackles_won",
    265: "aerial_duels_won_attacking",
    266: "aerial_duels_won_defending",
    267: "good_direction_saves",
}


def parse_event_string(s: str) -> dict[int, int]:
    """Parse an EA event aggregate string like '24:12,30:6' into {event_id: count}."""
    result: dict[int, int] = {}
    if not s or not s.strip():
        return result
    for pair in s.split(","):
        pair = pair.strip()
        if not pair:
            continue
        parts = pair.split(":")
        if len(parts) != 2:
            continue
        try:
            eid = int(parts[0])
            count = int(parts[1])
            result[eid] = result.get(eid, 0) + count
        except ValueError:
            continue
    return result


def decode_events(player: dict[str, object]) -> tuple[dict[int, int], dict[str, int]]:
    """Decode the four match_event_aggregate buckets from a player dict.

    Returns:
        raw_events: aggregated {event_id: total_count} across all 4 buckets
        named_metrics: {metric_name: value} for events that exist in EVENT_MAP,
                       plus derived composite metrics.
    """
    raw_events: dict[int, int] = {}

    for bucket in range(4):
        key = f"match_event_aggregate_{bucket}"
        raw_str = player.get(key, "") or ""
        bucket_events = parse_event_string(str(raw_str))
        for eid, count in bucket_events.items():
            raw_events[eid] = raw_events.get(eid, 0) + count

    named_metrics: dict[str, int] = {}
    for eid, name in EVENT_MAP.items():
        if eid in raw_events:
            # If two event IDs map to same name, accumulate
            named_metrics[name] = named_metrics.get(name, 0) + raw_events[eid]

    # Derived composites — override the direct-mapped values with correct formula
    # fouls_conceded = event_2 + event_3
    fouls_conceded = raw_events.get(2, 0) + raw_events.get(3, 0)
    if fouls_conceded:
        named_metrics["fouls_conceded"] = fouls_conceded

    # yellow_cards = event_95 + event_213
    yellow_cards = raw_events.get(95, 0) + raw_events.get(213, 0)
    if yellow_cards:
        named_metrics["yellow_cards"] = yellow_cards

    return raw_events, named_metrics
