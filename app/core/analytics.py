"""Analytics layer: everything the dashboard/API derives from stored data.

Design rules
------------
* EA season data (``club_overall_stats``) and the local match sample are never
  mixed into the same number: they are returned side by side and labelled.
* Event-based metrics only exist for league/playoff payloads. When a match has
  no telemetry the derived numbers are ``0`` with ``has_events = False``, so the
  UI can explain the gap instead of showing fake zeroes.
* Team-level event aggregates from EA are empty, so team event totals are summed
  from the players who actually played.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.member_link import normalize_name
from app.core.review import COUNTED_STATUSES, is_counted
from app.db.models import (
    Club,
    ClubMatch,
    ClubMember,
    ClubMemberCareer,
    ClubOverallStats,
    Match,
    MatchTeamStats,
    PlayerMatchEvent,
    PlayerMatchMetrics,
    PlayerMatchStats,
)

# EA result codes found in overallStats.lastMatchN, validated against
# clubs/matches results for the same club: 1 win, 2 loss, 3 draw, -1 no match.
RESULT_CODES = {1: "W", 2: "L", 3: "D"}
RESULT_LETTER = {"W": "V", "D": "E", "L": "D"}

MATCH_TYPE_LABELS = {
    "1": "Liga",
    "2": "Playoff",
    "3": "Playoff",
    "4": "Playoff",
    "5": "Amistoso",
    "leagueMatch": "Liga",
    "playoffMatch": "Playoff",
    "friendlyMatch": "Amistoso",
}
MATCH_TYPE_SHORT = {
    "1": "LIG",
    "2": "PLY",
    "3": "PLY",
    "4": "PLY",
    "5": "AMI",
    "leagueMatch": "LIG",
    "playoffMatch": "PLY",
    "friendlyMatch": "AMI",
}
MATCH_TYPE_FILTERS = {
    "league": ("1", "leagueMatch"),
    "playoff": ("2", "3", "4", "playoffMatch"),
    "friendly": ("5", "friendlyMatch"),
}
MATCH_TYPE_FILTER_LABELS = {
    "league": "Liga",
    "playoff": "Playoff",
    "friendly": "Amistoso",
}
RESULT_FILTER_LABELS = {"win": "Vitória", "draw": "Empate", "loss": "Derrota"}
MARGIN_FILTER_LABELS = {"1": "1 gol", "2": "2 gols", "3+": "3+ gols"}

POSITION_LABELS = {
    "GK": "Goleiro",
    "DEF": "Defensor",
    "MID": "Meio-campista",
    "FWD": "Atacante",
    "UNK": "—",
}
POSITION_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3, "UNK": 4}

# Fields summed from player_match_metrics (event telemetry).
METRIC_FIELDS: tuple[str, ...] = (
    "passes_completed",
    "passes_failed",
    "passes_completed_forward",
    "passes_completed_backward",
    "passes_completed_sideways",
    "passes_completed_short",
    "passes_completed_medium",
    "passes_completed_long",
    "passes_completed_cross",
    "passes_failed_forward",
    "passes_failed_backward",
    "passes_failed_sideways",
    "first_time_passes",
    "switches_of_play",
    "through_passes",
    "offside_passes",
    "shots_on_target",
    "shots_off_target",
    "shots_on_target_inside_box",
    "shots_on_target_outside_box",
    "shots_saved",
    "goals_by_events",
    "goals_first_time",
    "second_assists",
    "through_ball_assists",
    "dribbles_completed",
    "dribbles_carried",
    "dribbles_beat",
    "dribbles_skill_move_beat",
    "tackles_won",
    "tackles_lost",
    "standing_tackles_won",
    "sliding_tackles_won",
    "clean_tackles",
    "dangerous_tackles",
    "interceptions",
    "opponents_dispossessed",
    "crosses_blocked",
    "aerial_duels_won_attacking",
    "aerial_duels_won_defending",
    "good_direction_saves",
    "possession_lost_defensive_third",
    "possession_lost_middle_third",
    "possession_lost_attacking_third",
    "possession_won_defensive_third",
    "possession_won_middle_third",
    "possession_won_attacking_third",
    "fouls_conceded",
    "fouls_conceded_defensive_third",
    "fouls_won",
    "yellow_cards",
    "penalties_conceded",
    "corners_conceded",
    "corners_taken",
    "in_position",
    "out_of_position",
    "oop_severity_1",
    "oop_severity_2",
    "oop_severity_3",
    "oop_severity_4",
    "oop_severity_5",
    "feedback_use_the_ball",
    "feedback_pick_your_pass",
    "feedback_no_good_option",
    "feedback_good_option_taken",
    "feedback_best_option_taken",
    "feedback_should_have_passed_elsewhere",
    "feedback_chose_to_pass",
)


# Portuguese labels for the event metrics shown in the UI.
METRIC_LABELS: dict[str, str] = {
    "passes_completed": "Passes certos",
    "passes_failed": "Passes errados",
    "passes_completed_forward": "Passes certos à frente",
    "passes_completed_backward": "Passes certos atrás",
    "passes_completed_sideways": "Passes certos para o lado",
    "switches_of_play": "Mudanças de jogo",
    "through_passes": "Passes em profundidade",
    "offside_passes": "Passes para impedimento",
    "shots_on_target": "Chutes no gol",
    "shots_off_target": "Chutes para fora",
    "shots_on_target_inside_box": "Chutes no gol na área",
    "shots_on_target_outside_box": "Chutes no gol de fora",
    "shots_saved": "Chutes defendidos",
    "goals_by_events": "Gols (eventos)",
    "goals_first_time": "Gols de primeira",
    "second_assists": "Pré-assistências",
    "through_ball_assists": "Assistências em profundidade",
    "dribbles_completed": "Dribles certos",
    "dribbles_carried": "Conduções",
    "dribbles_beat": "Dribles superados",
    "dribbles_skill_move_beat": "Dribles com skill move",
    "tackles_won": "Desarmes ganhos",
    "tackles_lost": "Desarmes perdidos",
    "standing_tackles_won": "Desarmes de pé",
    "sliding_tackles_won": "Carrinhos certos",
    "clean_tackles": "Desarmes limpos",
    "dangerous_tackles": "Desarmes perigosos",
    "interceptions": "Interceptações",
    "opponents_dispossessed": "Adversários desarmados",
    "crosses_blocked": "Cruzamentos bloqueados",
    "aerial_duels_won_attacking": "Duelos aéreos ofensivos",
    "aerial_duels_won_defending": "Duelos aéreos defensivos",
    "good_direction_saves": "Defesas bem posicionadas",
    "possession_lost_defensive_third": "Posse perdida no campo defensivo",
    "possession_lost_middle_third": "Posse perdida no meio",
    "possession_lost_attacking_third": "Posse perdida no ataque",
    "possession_won_defensive_third": "Posse recuperada no campo defensivo",
    "possession_won_middle_third": "Posse recuperada no meio",
    "possession_won_attacking_third": "Posse recuperada no ataque",
    "fouls_conceded": "Faltas cometidas",
    "fouls_won": "Faltas sofridas",
    "yellow_cards": "Cartões amarelos",
    "penalties_conceded": "Pênaltis cometidos",
    "corners_conceded": "Escanteios cedidos",
    "corners_taken": "Escanteios cobrados",
    "in_position": "Bem posicionado",
    "out_of_position": "Fora de posição",
    "feedback_good_option_taken": "Boa opção escolhida",
    "feedback_best_option_taken": "Melhor opção escolhida",
    "feedback_use_the_ball": "Uso da bola",
    "feedback_pick_your_pass": "Escolha de passe",
    "feedback_no_good_option": "Sem boa opção",
    "feedback_should_have_passed_elsewhere": "Deveria ter passado em outro lugar",
    "feedback_chose_to_pass": "Escolheu passar",
}


def event_coverage(db: Session, match_ids: Sequence[str]) -> dict[str, Any]:
    """How much of the raw event stream the decoder maps to named metrics."""
    from app.core.decoder import EVENT_MAP  # local import: avoid a cycle

    if not match_ids:
        return {
            "mapped": 0,
            "orphan": 0,
            "total": 0,
            "mapped_pct": None,
            "orphan_codes": [],
        }

    rows = (
        db.query(
            PlayerMatchEvent.event_id,
            func.sum(PlayerMatchEvent.count).label("total"),
        )
        .filter(PlayerMatchEvent.match_id.in_(list(match_ids)))
        .group_by(PlayerMatchEvent.event_id)
        .all()
    )
    mapped = 0
    orphan = 0
    orphan_codes: list[dict[str, int]] = []
    for event_id, total in rows:
        count = int(total or 0)
        if int(event_id) in EVENT_MAP:
            mapped += count
        else:
            orphan += count
            orphan_codes.append({"event_id": int(event_id), "count": count})
    orphan_codes.sort(key=lambda item: -item["count"])
    total = mapped + orphan
    return {
        "mapped": mapped,
        "orphan": orphan,
        "total": total,
        "mapped_pct": _round(safe_div(mapped, total) * 100, 1) if total else None,
        "orphan_codes": orphan_codes[:12],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def per90(value: float, seconds: float) -> float:
    return safe_div(value * 90.0 * 60.0, seconds) if seconds else 0.0


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def index_from(value: float, reference: float) -> float:
    """Turn a per-90 value into a 0-100 radar axis using a reference."""
    if reference <= 0:
        return 0.0
    return round(clamp(value / reference * 100.0), 1)


def parse_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def position_group(position: str | None, pro_pos: int | None = None) -> str:
    text = (position or "").strip().lower()
    if text in ("goalkeeper", "gk", "goleiro"):
        return "GK"
    if text in ("defender", "defence", "def", "zagueiro", "lateral"):
        return "DEF"
    if text in ("midfielder", "midfield", "mid", "meia"):
        return "MID"
    if text in ("forward", "striker", "attacker", "fwd", "atacante"):
        return "FWD"
    if pro_pos is not None:
        return {0: "GK", 5: "DEF", 14: "MID", 25: "FWD"}.get(int(pro_pos), "UNK")
    return "UNK"


def position_label(group: str) -> str:
    return POSITION_LABELS.get(group, group)


def match_type_label(code: str | None) -> str:
    if code is None:
        return "—"
    return MATCH_TYPE_LABELS.get(str(code), f"Tipo {code}")


def match_type_short(code: str | None) -> str:
    if code is None:
        return "—"
    return MATCH_TYPE_SHORT.get(str(code), str(code))


def match_type_key(code: str | None) -> str:
    code = str(code or "")
    for key, codes in MATCH_TYPE_FILTERS.items():
        if code in codes:
            return key
    return "other"


def normalize_match_type(code: str | None) -> str | None:
    """Map both EA numeric codes and string names to the canonical key."""
    key = match_type_key(code)
    return None if key == "other" else key


def result_letter(club_match: ClubMatch) -> str:
    if club_match.is_win:
        return "W"
    if club_match.is_draw:
        return "D"
    return "L"


def minutes(seconds: int | None) -> int:
    return int((seconds or 0) // 60)


def _streak(form_letters: Sequence[str]) -> dict[str, object]:
    """Current streak from a newest-first list of W/D/L letters."""
    if not form_letters:
        return {"letter": None, "label": "—", "count": 0}
    first = form_letters[0]
    count = 0
    for letter in form_letters:
        if letter != first:
            break
        count += 1
    return {"letter": first, "label": RESULT_LETTER.get(first, first), "count": count}


# ─────────────────────────────────────────────────────────────────────────────
# Filters
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MatchFilters:
    match_type: str | None = None
    days: int | None = None
    last_n: int | None = None
    result: str | None = None
    margin: str | None = None
    opponent: str | None = None

    @classmethod
    def from_query(cls, params: Mapping[str, Any]) -> "MatchFilters":
        def _int(name: str) -> int | None:
            raw = params.get(name)
            if raw in (None, ""):
                return None
            try:
                value = int(str(raw))
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None

        match_type = str(params.get("tipo") or "") or None
        if match_type not in MATCH_TYPE_FILTERS:
            match_type = None
        result = str(params.get("resultado") or "") or None
        if result not in RESULT_FILTER_LABELS:
            result = None
        margin = str(params.get("margem") or "") or None
        if margin not in MARGIN_FILTER_LABELS:
            margin = None
        opponent = str(params.get("adversario") or "") or None
        return cls(
            match_type=match_type,
            days=_int("dias"),
            last_n=_int("n"),
            result=result,
            margin=margin,
            opponent=opponent,
        )

    def as_query(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.match_type:
            values["tipo"] = self.match_type
        if self.days:
            values["dias"] = str(self.days)
        if self.last_n:
            values["n"] = str(self.last_n)
        if self.result:
            values["resultado"] = self.result
        if self.margin:
            values["margem"] = self.margin
        if self.opponent:
            values["adversario"] = self.opponent
        return values

    @property
    def is_active(self) -> bool:
        return bool(self.as_query())

    def describe(self) -> list[str]:
        parts: list[str] = []
        if self.match_type:
            parts.append(MATCH_TYPE_FILTER_LABELS.get(self.match_type, self.match_type))
        if self.days:
            parts.append(f"últimos {self.days} dias")
        if self.last_n:
            parts.append(f"últimos {self.last_n} jogos")
        if self.result:
            parts.append(RESULT_FILTER_LABELS.get(self.result, self.result))
        if self.margin:
            parts.append(f"margem {MARGIN_FILTER_LABELS.get(self.margin, self.margin)}")
        if self.opponent:
            parts.append("vs adversário")
        return parts


def filtered_matches(
    db: Session, club_id: str, filters: MatchFilters
) -> list[tuple[ClubMatch, Match]]:
    """Counted matches for a club, newest first, with every filter applied."""
    query = (
        db.query(ClubMatch, Match)
        .join(Match, Match.match_id == ClubMatch.match_id)
        .filter(
            ClubMatch.club_id == club_id,
            ClubMatch.is_ignored == 0,
            Match.review_status.in_(COUNTED_STATUSES),
        )
    )
    if filters.match_type:
        query = query.filter(
            Match.match_type_code.in_(MATCH_TYPE_FILTERS[filters.match_type])
        )
    if filters.days:
        cutoff = (_now() - timedelta(days=filters.days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        query = query.filter(Match.played_at >= cutoff)
    if filters.opponent:
        query = query.filter(
            or_(
                Match.club_a_id == filters.opponent,
                Match.club_b_id == filters.opponent,
            )
        )
    if filters.result == "win":
        query = query.filter(ClubMatch.is_win == 1)
    elif filters.result == "draw":
        query = query.filter(ClubMatch.is_draw == 1)
    elif filters.result == "loss":
        query = query.filter(ClubMatch.is_loss == 1)

    rows = query.order_by(Match.played_at.desc()).all()

    if filters.margin:
        target = 3 if filters.margin == "3+" else int(filters.margin)
        rows = [
            (club_match, match)
            for club_match, match in rows
            if (
                abs((club_match.goals_for or 0) - (club_match.goals_against or 0)) >= 3
                if filters.margin == "3+"
                else abs(
                    (club_match.goals_for or 0) - (club_match.goals_against or 0)
                )
                == target
            )
        ]

    if filters.last_n:
        rows = rows[: filters.last_n]

    return rows


def opponent_id(match: Match, club_id: str) -> str:
    return match.club_b_id if match.club_a_id == club_id else match.club_a_id


# ─────────────────────────────────────────────────────────────────────────────
# Summaries
# ─────────────────────────────────────────────────────────────────────────────


def local_summary(rows: Sequence[tuple[ClubMatch, Match]]) -> dict[str, Any]:
    played = len(rows)
    wins = sum(1 for cm, _ in rows if cm.is_win)
    draws = sum(1 for cm, _ in rows if cm.is_draw)
    losses = sum(1 for cm, _ in rows if cm.is_loss)
    goals_for = sum((cm.goals_for or 0) for cm, _ in rows)
    goals_against = sum((cm.goals_against or 0) for cm, _ in rows)
    form_newest = [result_letter(cm) for cm, _ in rows]
    days = sorted({match.played_at[:10] for _, match in rows if match.played_at})

    return {
        "played": played,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "goal_diff": goals_for - goals_against,
        "win_rate": _round(safe_div(wins, played) * 100, 1),
        "points": wins * 3 + draws,
        "ppg": _round(safe_div(wins * 3 + draws, played), 2),
        "goals_per_game": _round(safe_div(goals_for, played), 2),
        "goals_against_per_game": _round(safe_div(goals_against, played), 2),
        "goal_diff_per_game": _round(safe_div(goals_for - goals_against, played), 2),
        "form": list(reversed(form_newest)),
        "form_newest": form_newest,
        "streak": _streak(form_newest),
        "sessions": len(days),
        "first_date": days[0] if days else None,
        "last_date": days[-1] if days else None,
    }


def official_summary(
    overall: ClubOverallStats | None, names: Mapping[str, str] | None = None
) -> dict[str, Any] | None:
    if overall is None:
        return None
    names = names or {}
    games = overall.games_played or 0
    wins = overall.wins or 0
    draws = overall.ties or 0
    goals = overall.goals or 0
    against = overall.goals_against or 0

    last_results = parse_json_list(overall.last_results)
    opponents = []
    for item in last_results:
        if not isinstance(item, dict):
            continue
        code = item.get("result")
        letter = RESULT_CODES.get(code, "?") if isinstance(code, int) else "?"
        if letter == "?":
            continue
        opponent = item.get("opponent")
        opponents.append(
            {
                "letter": letter,
                "label": RESULT_LETTER.get(letter, letter),
                "opponent_id": opponent,
                "opponent_name": names.get(str(opponent), str(opponent)),
            }
        )

    return {
        "games_played": games,
        "wins": wins,
        "draws": draws,
        "losses": overall.losses or 0,
        "goals": goals,
        "goals_against": against,
        "goal_diff": goals - against,
        "win_rate": _round(safe_div(wins, games) * 100, 1),
        "ppg": _round(safe_div(wins * 3 + draws, games), 2),
        "goals_per_game": _round(safe_div(goals, games), 2),
        "goals_against_per_game": _round(safe_div(against, games), 2),
        "skill_rating": overall.skill_rating,
        "reputation_tier": overall.reputation_tier,
        "promotions": overall.promotions or 0,
        "relegations": overall.relegations or 0,
        "best_division": overall.best_division,
        "best_finish_group": overall.best_finish_group,
        "win_streak": overall.win_streak or 0,
        "unbeaten_streak": overall.unbeaten_streak or 0,
        "league_appearances": overall.league_appearances,
        "playoff_games": overall.playoff_games or 0,
        "form": [item["letter"] for item in reversed(opponents)],
        "form_newest": [item["letter"] for item in opponents],
        "streak": _streak([item["letter"] for item in opponents]),
        "last_opponents": opponents,
        "division_finishes": parse_json_list(overall.division_finishes),
        "updated_at": overall.updated_at,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Player aggregates
# ─────────────────────────────────────────────────────────────────────────────


def _event_sums(
    db: Session, match_ids: Sequence[str], player_keys: Iterable[str]
) -> tuple[dict[str, dict[str, int]], set[str]]:
    """Sum event metrics per player. Returns (totals, players_with_telemetry)."""
    keys = list(player_keys)
    if not match_ids or not keys:
        return {}, set()

    rows = (
        db.query(PlayerMatchMetrics)
        .filter(
            PlayerMatchMetrics.match_id.in_(list(match_ids)),
            PlayerMatchMetrics.player_key.in_(keys),
        )
        .all()
    )

    totals: dict[str, dict[str, int]] = {}
    present: set[str] = set()
    for row in rows:
        bucket = totals.setdefault(row.player_key, {})
        for field in METRIC_FIELDS:
            value = getattr(row, field, None)
            if value is None:
                continue
            present.add(row.player_key)
            bucket[field] = bucket.get(field, 0) + int(value)
    return totals, present


def player_aggregates(
    db: Session,
    club_id: str,
    rows: Sequence[tuple[ClubMatch, Match]],
    *,
    min_seconds: int = 0,
) -> list[dict[str, Any]]:
    """Aggregate the filtered sample per player, with derived indices."""
    match_ids = [match.match_id for _, match in rows]
    if not match_ids:
        return []

    team_goals_by_match = {
        match.match_id: club_match.goals_for or 0 for club_match, match in rows
    }
    margins = {
        match.match_id: (club_match.goals_for or 0) - (club_match.goals_against or 0)
        for club_match, match in rows
    }
    matches_by_id = {match.match_id: match for _, match in rows}

    stats_rows = (
        db.query(PlayerMatchStats)
        .filter(
            PlayerMatchStats.club_id == club_id,
            PlayerMatchStats.match_id.in_(match_ids),
            PlayerMatchStats.seconds_played >= 1,
        )
        .all()
    )

    event_totals, telemetry_players = _event_sums(
        db, match_ids, {row.player_key for row in stats_rows}
    )

    # Member/career rows for the season snapshot, linked by player_key.
    members = {
        member.player_key: member
        for member in db.query(ClubMember).filter(
            ClubMember.club_id == club_id, ClubMember.player_key.isnot(None)
        )
    }
    careers = {
        career.player_key: career
        for career in db.query(ClubMemberCareer).filter(
            ClubMemberCareer.club_id == club_id,
            ClubMemberCareer.player_key.isnot(None),
        )
    }
    members_by_name = {
        normalize_name(member.name): member
        for member in db.query(ClubMember).filter(ClubMember.club_id == club_id)
    }

    grouped: dict[str, dict[str, Any]] = {}
    for row in stats_rows:
        if (row.seconds_played or 0) < min_seconds:
            continue
        entry = grouped.setdefault(
            row.player_key,
            {
                "player_key": row.player_key,
                "playername": row.playername,
                "matches": 0,
                "seconds": 0,
                "goals": 0,
                "assists": 0,
                "shots": 0,
                "passes_made": 0,
                "pass_attempts": 0,
                "tackles_made": 0,
                "tackle_attempts": 0,
                "saves": 0,
                "goals_conceded": 0,
                "clean_sheets": 0,
                "motm": 0,
                "red_cards": 0,
                "ratings": [],
                "clutch_ratings": [],
                "positions": {},
                "team_goals": 0,
                "last_match_id": row.match_id,
                "last_played_at": "",
                "best_rating": None,
            },
        )
        entry["matches"] += 1
        entry["seconds"] += row.seconds_played or 0
        entry["goals"] += row.goals or 0
        entry["assists"] += row.assists or 0
        entry["shots"] += row.shots or 0
        entry["passes_made"] += row.passes_made or 0
        entry["pass_attempts"] += row.pass_attempts or 0
        entry["tackles_made"] += row.tackles_made or 0
        entry["tackle_attempts"] += row.tackle_attempts or 0
        entry["saves"] += row.saves or 0
        entry["goals_conceded"] += row.goals_conceded or 0
        entry["clean_sheets"] += 1 if row.clean_sheets_any else 0
        entry["motm"] += 1 if row.is_mom else 0
        entry["red_cards"] += row.red_cards or 0
        entry["team_goals"] += team_goals_by_match.get(row.match_id, 0)

        played_at = matches_by_id.get(row.match_id).played_at if row.match_id in matches_by_id else ""
        if played_at >= entry["last_played_at"]:
            entry["last_played_at"] = played_at
            entry["last_match_id"] = row.match_id
            entry["playername"] = row.playername

        if row.rating is not None:
            entry["ratings"].append(float(row.rating))
            if abs(margins.get(row.match_id, 0)) <= 1:
                entry["clutch_ratings"].append(float(row.rating))
            if entry["best_rating"] is None or row.rating > entry["best_rating"]:
                entry["best_rating"] = float(row.rating)

        group = position_group(row.position, None)
        entry["positions"][group] = entry["positions"].get(group, 0) + 1

    players: list[dict[str, Any]] = []
    for key, entry in grouped.items():
        seconds = entry["seconds"]
        ratings: list[float] = entry["ratings"]
        avg_rating = _round(statistics.fmean(ratings), 2) if ratings else None
        stdev = statistics.pstdev(ratings) if len(ratings) > 1 else 0.0
        consistency = (
            _round(clamp(1 - safe_div(stdev, avg_rating)) * 100, 1)
            if avg_rating
            else None
        )
        clutch = (
            _round(statistics.fmean(entry["clutch_ratings"]) - avg_rating, 2)
            if entry["clutch_ratings"] and avg_rating is not None
            else None
        )
        dominant = max(entry["positions"], key=entry["positions"].get) if entry["positions"] else "UNK"
        goals = entry["goals"]
        assists = entry["assists"]
        involved = goals + assists
        events = event_totals.get(key, {})
        has_events = key in telemetry_players

        players.append(
            {
                "player_key": key,
                "playername": entry["playername"],
                "position_group": dominant,
                "position_label": position_label(dominant),
                "matches": entry["matches"],
                "minutes": int(seconds // 60),
                "seconds": seconds,
                "goals": goals,
                "assists": assists,
                "involvement": involved,
                "goals_per90": _round(per90(goals, seconds), 2),
                "assists_per90": _round(per90(assists, seconds), 2),
                "involvement_per90": _round(per90(involved, seconds), 2),
                "shots": entry["shots"],
                "passes_made": entry["passes_made"],
                "pass_attempts": entry["pass_attempts"],
                "pass_accuracy": _round(
                    safe_div(entry["passes_made"], entry["pass_attempts"]) * 100, 1
                )
                if entry["pass_attempts"]
                else None,
                "tackles_made": entry["tackles_made"],
                "tackle_attempts": entry["tackle_attempts"],
                "saves": entry["saves"],
                "goals_conceded": entry["goals_conceded"],
                "clean_sheets": entry["clean_sheets"],
                "motm": entry["motm"],
                "motm_per90": _round(per90(entry["motm"], seconds), 2),
                "red_cards": entry["red_cards"],
                "avg_rating": avg_rating,
                "best_rating": entry["best_rating"],
                "consistency": consistency,
                "clutch": clutch,
                "participation": _round(
                    safe_div(involved, entry["team_goals"]) * 100, 1
                )
                if entry["team_goals"]
                else None,
                "team_goals": entry["team_goals"],
                "last_match_id": entry["last_match_id"],
                "last_played_at": entry["last_played_at"],
                "has_events": has_events,
                "events": events,
                "passes_completed_events": events.get("passes_completed", 0),
                "dribbles_completed": events.get("dribbles_completed", 0),
                "dribbles_carried": events.get("dribbles_carried", 0),
                "second_assists": events.get("second_assists", 0),
                "tackles_won": events.get("tackles_won", 0),
                "interceptions": events.get("interceptions", 0),
                "possession_won_attacking_third": events.get(
                    "possession_won_attacking_third", 0
                ),
                "possession_won_defensive_third": events.get(
                    "possession_won_defensive_third", 0
                ),
                "possession_lost_attacking_third": events.get(
                    "possession_lost_attacking_third", 0
                ),
                "dribbles_per90": _round(
                    per90(events.get("dribbles_completed", 0), seconds), 2
                ),
                "passes_per90": _round(
                    per90(events.get("passes_completed", 0), seconds), 2
                ),
                "press_wins_per90": _round(
                    per90(events.get("possession_won_attacking_third", 0), seconds), 2
                ),
                "def_actions_per90": _round(
                    per90(
                        events.get("tackles_won", 0)
                        + events.get("interceptions", 0),
                        seconds,
                    ),
                    2,
                ),
                "member_overall": _member_value(members, key, "overall"),
                "member_games": _member_value(members, key, "games_played"),
                "member_goals": _member_value(members, key, "goals"),
                "member_assists": _member_value(members, key, "assists"),
                "member_rating": _member_value(members, key, "rating_avg"),
                "member_pass_pct": _member_value(members, key, "pass_success_rate"),
                "career_games": _member_value(careers, key, "games_played"),
                "career_goals": _member_value(careers, key, "goals"),
                "career_assists": _member_value(careers, key, "assists"),
                "career_motm": _member_value(careers, key, "man_of_the_match"),
                "has_member": key in members
                or normalize_name(entry["playername"]) in members_by_name,
            }
        )

    _apply_position_adjustment(players)
    _apply_power_index(players)
    players.sort(
        key=lambda item: (
            -float(item["power_index"] or 0),
            -int(item["minutes"]),
            str(item["playername"]).casefold(),
        )
    )
    for rank, player in enumerate(players, start=1):
        player["rank"] = rank
    return players


def _member_value(
    mapping: Mapping[str, Any], key: str, attribute: str
) -> Any | None:
    row = mapping.get(key)
    return getattr(row, attribute, None) if row is not None else None


def _apply_position_adjustment(players: list[dict[str, Any]]) -> None:
    groups: dict[str, list[float]] = {}
    for player in players:
        if player["avg_rating"] is not None:
            groups.setdefault(player["position_group"], []).append(player["avg_rating"])
    means = {group: statistics.fmean(values) for group, values in groups.items()}
    for player in players:
        rating = player["avg_rating"]
        mean = means.get(player["position_group"])
        player["position_avg_rating"] = _round(mean, 2) if mean else None
        player["rating_adj"] = (
            _round(rating - mean, 2) if rating is not None and mean else None
        )


def _zscore(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0 for _ in values]
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0:
        return [0.0 for _ in values]
    return [(value - mean) / stdev for value in values]


def _apply_power_index(players: list[dict[str, Any]]) -> None:
    """Composite 0-100 index: G+A/90, adjusted rating, MOTM/90, minutes."""
    if not players:
        return
    ga90 = _zscore([float(p["involvement_per90"] or 0) for p in players])
    adj = _zscore([float(p["rating_adj"] or 0) for p in players])
    motm = _zscore([float(p["motm_per90"] or 0) for p in players])
    mins = _zscore([float(p["minutes"] or 0) for p in players])
    normal = statistics.NormalDist()
    for player, z_ga, z_adj, z_motm, z_min in zip(players, ga90, adj, motm, mins):
        composite = 0.40 * z_ga + 0.25 * z_adj + 0.20 * z_motm + 0.15 * z_min
        player["power_score"] = _round(composite, 2)
        player["power_index"] = _round(clamp(normal.cdf(composite) * 100), 1)


def sort_players(
    players: list[dict[str, Any]], sort_by: str | None = None
) -> list[dict[str, Any]]:
    sorters = {
        "power": lambda p: (-float(p["power_index"] or 0), -int(p["minutes"])),
        "ga90": lambda p: (-float(p["involvement_per90"] or 0), -int(p["minutes"])),
        "rating": lambda p: (-float(p["avg_rating"] or 0), -int(p["minutes"])),
        "goals": lambda p: (-int(p["goals"]), -int(p["assists"])),
        "assists": lambda p: (-int(p["assists"]), -int(p["goals"])),
        "minutes": lambda p: (-int(p["minutes"]),),
        "name": lambda p: (str(p["playername"]).casefold(),),
    }
    key = sorters.get(sort_by or "power", sorters["power"])
    return sorted(players, key=key)


# ─────────────────────────────────────────────────────────────────────────────
# Team profile (events)
# ─────────────────────────────────────────────────────────────────────────────


def _team_minutes(
    db: Session, club_id: str, rows: Sequence[tuple[ClubMatch, Match]]
) -> float:
    """Sum of each match's longest appearance (accurate playing time)."""
    match_ids = [match.match_id for _, match in rows]
    if not match_ids:
        return 0.0
    per_match: dict[str, int] = {}
    for match_id, seconds in (
        db.query(PlayerMatchStats.match_id, PlayerMatchStats.seconds_played)
        .filter(
            PlayerMatchStats.club_id == club_id,
            PlayerMatchStats.match_id.in_(match_ids),
        )
        .all()
    ):
        per_match[match_id] = max(per_match.get(match_id, 0), seconds or 0)
    return sum(per_match.values()) / 60.0


def team_profile(
    db: Session,
    club_id: str,
    rows: Sequence[tuple[ClubMatch, Match]],
    players: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Team-level event profile, summed from players (EA team events are empty)."""
    match_ids = [match.match_id for _, match in rows]
    if not match_ids:
        return {"has_events": False, "matches_with_events": 0, "matches": 0}

    players = list(players) if players is not None else player_aggregates(
        db, club_id, rows
    )
    with_events = [p for p in players if p["has_events"]]

    totals: dict[str, int] = {}
    for player in with_events:
        for field, value in player["events"].items():
            totals[field] = totals.get(field, 0) + int(value)

    team_minutes = _team_minutes(db, club_id, rows)
    pass_volume = totals.get("passes_completed", 0)
    forward = totals.get("passes_completed_forward", 0)
    backward = totals.get("passes_completed_backward", 0)
    sideways = totals.get("passes_completed_sideways", 0)
    directional = forward + backward + sideways
    verticality = safe_div(forward, directional) * 100 if directional else 0.0

    press = per90(totals.get("possession_won_attacking_third", 0), team_minutes * 60)
    def_actions = per90(
        totals.get("tackles_won", 0) + totals.get("interceptions", 0),
        team_minutes * 60,
    )
    passes90 = per90(pass_volume, team_minutes * 60)
    sot90 = per90(totals.get("shots_on_target", 0), team_minutes * 60)
    dribbles90 = per90(totals.get("dribbles_completed", 0), team_minutes * 60)

    radar = [
        {"label": "Posse", "raw": _round(passes90, 2), "value": index_from(passes90, 350), "unit": "passes/90"},
        {"label": "Verticalidade", "raw": _round(verticality, 1), "value": round(clamp(verticality), 1), "unit": "%"},
        {"label": "Pressão alta", "raw": _round(press, 2), "value": index_from(press, 8), "unit": "recuperações/90"},
        {"label": "Solidez", "raw": _round(def_actions, 2), "value": index_from(def_actions, 25), "unit": "ações/90"},
        {"label": "Finalização", "raw": _round(sot90, 2), "value": index_from(sot90, 6), "unit": "chutes no gol/90"},
    ]

    thirds = {
        "won": {
            "defensive": totals.get("possession_won_defensive_third", 0),
            "middle": totals.get("possession_won_middle_third", 0),
            "attacking": totals.get("possession_won_attacking_third", 0),
        },
        "lost": {
            "defensive": totals.get("possession_lost_defensive_third", 0),
            "middle": totals.get("possession_lost_middle_third", 0),
            "attacking": totals.get("possession_lost_attacking_third", 0),
        },
    }

    matches_with_events = (
        db.query(PlayerMatchEvent.match_id)
        .filter(PlayerMatchEvent.match_id.in_(match_ids))
        .distinct()
        .count()
    )

    return {
        "has_events": bool(with_events),
        "matches": len(rows),
        "matches_with_events": matches_with_events,
        "team_minutes": _round(team_minutes, 1),
        "totals": totals,
        "per90": {
            "passes": _round(passes90, 2),
            "verticality": _round(verticality, 1),
            "press_wins": _round(press, 2),
            "def_actions": _round(def_actions, 2),
            "shots_on_target": _round(sot90, 2),
            "dribbles": _round(dribbles90, 2),
        },
        "radar": radar,
        "thirds": thirds,
        "pass_directions": [
            {"label": "Frente", "value": forward},
            {"label": "Lado", "value": sideways},
            {"label": "Trás", "value": backward},
        ],
        "pass_directions_total": directional,
        "event_totals": {
            "second_assists": totals.get("second_assists", 0),
            "dribbles_completed": totals.get("dribbles_completed", 0),
            "interceptions": totals.get("interceptions", 0),
            "tackles_won": totals.get("tackles_won", 0),
            "possession_won_attacking_third": totals.get(
                "possession_won_attacking_third", 0
            ),
            "shots_on_target": totals.get("shots_on_target", 0),
        },
    }


def pass_direction_players(
    players: Sequence[dict[str, Any]], limit: int = 12
) -> list[dict[str, Any]]:
    """Per-player completed passes by direction, for the stacked bar chart."""
    out: list[dict[str, Any]] = []
    for player in players:
        events = player.get("events") or {}
        forward = int(events.get("passes_completed_forward", 0))
        backward = int(events.get("passes_completed_backward", 0))
        sideways = int(events.get("passes_completed_sideways", 0))
        total = forward + backward + sideways
        if total <= 0:
            continue
        out.append(
            {
                "player_key": player["player_key"],
                "playername": player["playername"],
                "forward": forward,
                "sideways": sideways,
                "backward": backward,
                "total": total,
                "verticality": _round(safe_div(forward, total) * 100, 1),
            }
        )
    out.sort(key=lambda item: -int(item["total"]))
    return out[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────────────────────────────────────


def session_reports(
    db: Session,
    club_id: str,
    rows: Sequence[tuple[ClubMatch, Match]],
) -> list[dict[str, Any]]:
    """Group the filtered sample by calendar day and grade each session."""
    if not rows:
        return []

    by_day: dict[str, list[tuple[ClubMatch, Match]]] = {}
    for club_match, match in rows:
        by_day.setdefault((match.played_at or "")[:10], []).append((club_match, match))

    match_ids = [match.match_id for _, match in rows]
    stat_rows = (
        db.query(PlayerMatchStats)
        .filter(
            PlayerMatchStats.club_id == club_id,
            PlayerMatchStats.match_id.in_(match_ids),
            PlayerMatchStats.seconds_played >= 1,
        )
        .all()
    )
    stats_by_match: dict[str, list[PlayerMatchStats]] = {}
    for stat in stat_rows:
        stats_by_match.setdefault(stat.match_id, []).append(stat)

    sessions: list[dict[str, Any]] = []
    for day, day_rows in by_day.items():
        wins = sum(1 for cm, _ in day_rows if cm.is_win)
        draws = sum(1 for cm, _ in day_rows if cm.is_draw)
        losses = sum(1 for cm, _ in day_rows if cm.is_loss)
        goals_for = sum((cm.goals_for or 0) for cm, _ in day_rows)
        goals_against = sum((cm.goals_against or 0) for cm, _ in day_rows)
        ratings: list[float] = []
        best: tuple[str, float] | None = None
        scorers: dict[str, int] = {}

        for cm, match in day_rows:
            for stat in stats_by_match.get(match.match_id, []):
                if stat.rating is not None:
                    ratings.append(float(stat.rating))
                    if best is None or stat.rating > best[1]:
                        best = (stat.playername, float(stat.rating))
                if stat.goals:
                    scorers[stat.playername] = scorers.get(stat.playername, 0) + stat.goals

        played = len(day_rows)
        avg_rating = _round(statistics.fmean(ratings), 2) if ratings else None
        result_score = safe_div(wins * 3 + draws, played * 3)
        gd_score = clamp((safe_div(goals_for - goals_against, played) + 2) / 4, 0, 1)
        rating_score = clamp(safe_div((avg_rating or 5) - 5, 5), 0, 1)
        grade = round(100 * (0.5 * result_score + 0.3 * gd_score + 0.2 * rating_score))

        top_scorer = max(scorers.items(), key=lambda item: item[1]) if scorers else None
        sessions.append(
            {
                "date": day,
                "played": played,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "goals_for": goals_for,
                "goals_against": goals_against,
                "goal_diff": goals_for - goals_against,
                "win_rate": _round(safe_div(wins, played) * 100, 1),
                "avg_rating": avg_rating,
                "grade": int(grade),
                "best_player": best[0] if best else None,
                "best_rating": best[1] if best else None,
                "top_scorer": top_scorer[0] if top_scorer else None,
                "top_scorer_goals": top_scorer[1] if top_scorer else 0,
                "matches": [
                    {
                        "match_id": match.match_id,
                        "opponent_id": opponent_id(match, club_id),
                        "goals_for": cm.goals_for,
                        "goals_against": cm.goals_against,
                        "letter": result_letter(cm),
                    }
                    for cm, match in day_rows
                ],
            }
        )

    sessions.sort(key=lambda item: item["date"], reverse=True)
    return sessions


# ─────────────────────────────────────────────────────────────────────────────
# Squad (EA member stats + local sample)
# ─────────────────────────────────────────────────────────────────────────────


def squad_view(
    db: Session,
    club_id: str,
    players: Sequence[dict[str, Any]],
    *,
    platform: str = "common-gen5",
) -> dict[str, Any]:
    """Members (EA season) merged with the local sample by player_key/name."""
    members = (
        db.query(ClubMember)
        .filter(ClubMember.club_id == club_id, ClubMember.platform == platform)
        .order_by(ClubMember.overall.desc())
        .all()
    )
    careers = {
        normalise_key(career.name): career
        for career in db.query(ClubMemberCareer).filter(
            ClubMemberCareer.club_id == club_id,
            ClubMemberCareer.platform == platform,
        )
    }

    by_key = {player["player_key"]: player for player in players}
    by_name = {
        normalize_name(player["playername"]): player for player in players
    }

    cards: list[dict[str, Any]] = []
    linked_keys: set[str] = set()
    for member in members:
        local = by_key.get(member.player_key) if member.player_key else None
        if local is None:
            local = by_name.get(normalize_name(member.name))
        if local is not None:
            linked_keys.add(local["player_key"])
        group = position_group(member.position, member.pro_pos)
        prev_goals = parse_json_list(member.prev_goals)
        cards.append(
            {
                "member": member,
                "career": careers.get(normalize_name(member.name)),
                "local": local,
                "position_group": group,
                "position_label": position_label(group),
                "prev_goals": prev_goals,
                "season_ga_per90": _round(
                    safe_div((member.goals or 0) + (member.assists or 0), member.games_played or 0),
                    2,
                ),
                "is_linked": local is not None,
            }
        )

    # Local players that have no EA member row (subs/guests) stay visible.
    extras = [
        {
            "member": None,
            "career": None,
            "local": player,
            "position_group": player["position_group"],
            "position_label": player["position_label"],
            "prev_goals": [],
            "season_ga_per90": None,
            "is_linked": False,
        }
        for player in players
        if player["player_key"] not in linked_keys
    ]

    linked = [card for card in cards if card["is_linked"]]
    unlinked = [card for card in cards if not card["is_linked"]]
    return {
        "cards": cards + extras,
        "members": cards,
        "linked": linked,
        "unlinked": unlinked,
        "extras": extras,
        "counts": {
            "members": len(cards),
            "linked": len(linked),
            "unlinked": len(unlinked),
            "local_only": len(extras),
        },
        "position_counts": _position_counts(cards),
    }


def normalise_key(name: str | None) -> str:
    return normalize_name(name)


def _position_counts(cards: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0, "UNK": 0}
    for card in cards:
        group = str(card.get("position_group") or "UNK")
        counts[group] = counts.get(group, 0) + 1
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# Overview / dashboard
# ─────────────────────────────────────────────────────────────────────────────


def recent_matches(
    rows: Sequence[tuple[ClubMatch, Match]],
    names: Mapping[str, str],
    club_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for club_match, match in rows[:limit]:
        opp = opponent_id(match, club_id)
        out.append(
            {
                "match_id": match.match_id,
                "played_at": match.played_at,
                "opponent_id": opp,
                "opponent_name": names.get(opp, opp),
                "goals_for": club_match.goals_for,
                "goals_against": club_match.goals_against,
                "letter": result_letter(club_match),
                "is_win": club_match.is_win,
                "is_draw": club_match.is_draw,
                "is_loss": club_match.is_loss,
                "type_code": match.match_type_code,
                "type_label": match_type_label(match.match_type_code),
                "type_short": match_type_short(match.match_type_code),
                "review_status": match.review_status,
                "dnf": match.dnf,
            }
        )
    return out


def goals_chart(rows: Sequence[tuple[ClubMatch, Match]], club_id: str, names: Mapping[str, str]) -> dict[str, Any]:
    """Chronological goals for/against plus a 5-match moving average."""
    ordered = list(reversed(rows))
    goals_for = [cm.goals_for or 0 for cm, _ in ordered]
    goals_against = [cm.goals_against or 0 for cm, _ in ordered]
    labels = [
        names.get(opponent_id(match, club_id), opponent_id(match, club_id))[:14]
        for _, match in ordered
    ]
    return {
        "labels": labels,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "moving_for": moving_average(goals_for, 5),
        "moving_against": moving_average(goals_against, 5),
    }


def moving_average(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for index in range(len(values)):
        start = max(0, index - window + 1)
        chunk = values[start : index + 1]
        out.append(round(statistics.fmean(chunk), 2) if chunk else None)
    return out


def dashboard_context(
    db: Session,
    club_id: str,
    platform: str,
    rows: Sequence[tuple[ClubMatch, Match]],
    names: Mapping[str, str],
    filters: MatchFilters | None = None,
) -> dict[str, Any]:
    overall = db.get(
        ClubOverallStats, {"club_id": club_id, "platform": platform}
    )
    official = official_summary(overall, names)
    local = local_summary(rows)
    players = player_aggregates(db, club_id, rows)
    return {
        "official": official,
        "local": local,
        "form": official["form"] if official else local["form"],
        "recent": recent_matches(rows, names, club_id),
        "top_players": players[:5],
        "goals_chart": goals_chart(rows, club_id, names),
        "last_opponents": (official or {}).get("last_opponents", []),
        "filters": filters or MatchFilters(),
        "has_official": official is not None,
        "has_events": any(player["has_events"] for player in players),
    }


# ─────────────────────────────────────────────────────────────────────────────
# H2H
# ─────────────────────────────────────────────────────────────────────────────


def h2h_summary(
    rows: Sequence[tuple[ClubMatch, Match]],
    club_id: str,
    opponent: str,
) -> dict[str, Any]:
    meetings = [
        (club_match, match)
        for club_match, match in rows
        if opponent_id(match, club_id) == opponent
    ]
    summary = local_summary(meetings)
    summary["opponent_id"] = opponent
    summary["meetings"] = [
        {
            "match_id": match.match_id,
            "played_at": match.played_at,
            "goals_for": cm.goals_for,
            "goals_against": cm.goals_against,
            "letter": result_letter(cm),
            "type_label": match_type_label(match.match_type_code),
        }
        for cm, match in meetings
    ]
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Match detail analytics
# ─────────────────────────────────────────────────────────────────────────────


def match_analytics(
    db: Session, match_id: str, club_ids: Sequence[str]
) -> dict[str, Any]:
    """Team comparison and per-player event metrics for one match."""
    stats_by_club: dict[str, dict[str, Any]] = {}
    team_rows = db.query(MatchTeamStats).filter_by(match_id=match_id).all()
    for row in team_rows:
        stats_by_club[row.club_id] = {
            "possession_pct": row.possession_pct,
            "shots": row.shots,
            "shots_on_target": row.shots_on_target,
            "passes_made": row.passes_made,
            "pass_attempts": row.pass_attempts,
            "pass_accuracy": _round(
                safe_div(row.passes_made or 0, row.pass_attempts or 0) * 100, 1
            )
            if row.pass_attempts
            else None,
            "tackles_made": row.tackles_made,
            "tackle_attempts": row.tackle_attempts,
            "fouls": row.fouls,
            "corners": row.corners,
            "saves": row.saves,
            "goals": row.goals,
        }

    player_keys = [
        key
        for (key,) in db.query(PlayerMatchStats.player_key)
        .filter(PlayerMatchStats.match_id == match_id)
        .all()
    ]
    event_totals, with_events = _event_sums(db, [match_id], player_keys)

    per_club_events: dict[str, dict[str, int]] = {}
    players = (
        db.query(PlayerMatchStats)
        .filter(PlayerMatchStats.match_id == match_id)
        .all()
    )
    for player in players:
        club_bucket = per_club_events.setdefault(
            player.club_id,
            {"dribbles_completed": 0, "interceptions": 0, "tackles_won": 0, "possession_won_attacking_third": 0},
        )
        events = event_totals.get(player.player_key, {})
        for field in club_bucket:
            club_bucket[field] += int(events.get(field, 0))

    for club_id in club_ids:
        stats_by_club.setdefault(club_id, {})
        stats_by_club[club_id]["events"] = per_club_events.get(club_id, {})

    labels = [
        ("Posse de bola", "possession_pct", "%"),
        ("Finalizações", "shots", ""),
        ("No alvo", "shots_on_target", ""),
        ("Passes certos", "passes_made", ""),
        ("Precisão de passe", "pass_accuracy", "%"),
        ("Desarmes", "tackles_made", ""),
        ("Faltas", "fouls", ""),
        ("Escanteios", "corners", ""),
        ("Defesas", "saves", ""),
    ]
    if with_events:
        labels += [
            ("Dribles certos", "dribbles_completed", ""),
            ("Interceptações", "interceptions", ""),
            ("Recuperações no ataque", "possession_won_attacking_third", ""),
        ]

    comparison = []
    for label, field, unit in labels:
        if field in ("dribbles_completed", "interceptions", "possession_won_attacking_third"):
            values = [
                (stats_by_club.get(cid, {}).get("events", {}) or {}).get(field, 0)
                for cid in club_ids
            ]
        else:
            values = [stats_by_club.get(cid, {}).get(field) for cid in club_ids]
        comparison.append(
            {
                "label": label,
                "unit": unit,
                "a": values[0] if values else None,
                "b": values[1] if len(values) > 1 else None,
            }
        )

    return {
        "has_events": bool(with_events),
        "stats": stats_by_club,
        "comparison": comparison,
        "events": event_totals,
        "players_with_events": len(with_events),
    }
