"""Domain helpers for private championships.

The EA payload only describes friendlies. These helpers add the external
context: which private competition a friendly belongs to, how the campaign is
going and who the top scorers are.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Championship, Club, ClubMatch, Match, MatchClassification, PlayerMatchStats
from app.core.review import is_counted, review_view
from app.core.analytics import match_type_label

TAG_SEPARATOR = ","


def now_iso() -> str:
    """Current UTC timestamp in the same format used across the database."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def serialize_tags(tags: Sequence[str] | str | None) -> str | None:
    """Normalise tags into a JSON array string (or None when empty)."""
    if tags is None:
        return None
    if isinstance(tags, str):
        raw_values = tags.split(TAG_SEPARATOR)
    else:
        raw_values = list(tags)

    cleaned: list[str] = []
    for value in raw_values:
        tag = str(value).strip()
        if tag and tag not in cleaned:
            cleaned.append(tag)
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def parse_tags(raw: str | None) -> list[str]:
    """Read tags written by :func:`serialize_tags`, tolerating legacy text."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return [part.strip() for part in raw.split(TAG_SEPARATOR) if part.strip()]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def parse_extra(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def serialize_extra(value: dict[str, object] | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def get_classification(session: Session, match_id: str) -> MatchClassification | None:
    return session.get(MatchClassification, match_id)


def classification_map(session: Session, match_ids: Iterable[str]) -> dict[str, dict[str, object]]:
    """Return {match_id: classification view} for the given matches."""
    ids = [mid for mid in match_ids if mid]
    if not ids:
        return {}

    rows = (
        session.query(MatchClassification, Championship)
        .join(Championship, Championship.id == MatchClassification.championship_id)
        .filter(MatchClassification.match_id.in_(ids))
        .all()
    )
    result: dict[str, dict[str, object]] = {}
    for classification, championship in rows:
        result[classification.match_id] = {
            "match_id": classification.match_id,
            "championship_id": championship.id,
            "championship_name": championship.name,
            "championship_color": championship.color or "#3b82f6",
            "championship_status": championship.status,
            "stage": classification.stage,
            "round": classification.round,
            "leg": classification.leg,
            "group_name": classification.group_name,
            "notes": classification.notes,
            "tags": parse_tags(classification.tags),
        }
    return result


def build_campaign(rows: Iterable[tuple[ClubMatch, Match]]) -> dict[str, object]:
    """Aggregate wins/draws/losses/goals from already-filtered club matches.

    Matches whose result is ignored or still waiting for review are excluded.
    """
    played = wins = draws = losses = goals_for = goals_against = 0
    for club_match, _match in rows:
        if club_match.is_ignored or not is_counted(_match.review_status):
            continue
        played += 1
        goals_for += club_match.goals_for or 0
        goals_against += club_match.goals_against or 0
        if club_match.is_win:
            wins += 1
        elif club_match.is_draw:
            draws += 1
        else:
            losses += 1

    points = wins * 3 + draws
    return {
        "played": played,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "goal_diff": goals_for - goals_against,
        "points": points,
        "points_per_game": round(points / played, 2) if played else 0.0,
        "points_pct": round(points / (played * 3) * 100, 1) if played else 0.0,
        "win_rate": round(wins / played * 100, 1) if played else 0.0,
    }


def top_scorers(
    session: Session,
    club_id: str,
    match_ids: Sequence[str],
    limit: int = 10,
) -> list[dict[str, object]]:
    """Best scorers of the club inside the classified matches."""
    if not match_ids:
        return []

    rows = (
        session.query(
            PlayerMatchStats.player_key,
            PlayerMatchStats.playername,
            func.count(PlayerMatchStats.match_id).label("matches"),
            func.sum(PlayerMatchStats.goals).label("goals"),
            func.sum(PlayerMatchStats.assists).label("assists"),
        )
        .join(Match, Match.match_id == PlayerMatchStats.match_id)
        .filter(
            PlayerMatchStats.club_id == club_id,
            PlayerMatchStats.match_id.in_(list(match_ids)),
            Match.review_status.in_(("valid", "corrected")),
        )
        .group_by(PlayerMatchStats.player_key, PlayerMatchStats.playername)
        .all()
    )

    scorers = [
        {
            "player_key": row.player_key,
            "playername": row.playername,
            "matches": row.matches or 0,
            "goals": row.goals or 0,
            "assists": row.assists or 0,
        }
        for row in rows
    ]
    scorers.sort(
        key=lambda item: (-int(item["goals"]), -int(item["assists"]), str(item["playername"]))
    )
    return scorers[:limit]


def opponent_summary(
    session: Session,
    club_id: str,
    rows: Sequence[tuple[ClubMatch, Match]],
) -> list[dict[str, object]]:
    """W/D/L record of the club against each opponent in the competition."""
    opponents: dict[str, dict[str, object]] = {}
    for club_match, match in rows:
        if club_match.is_ignored or not is_counted(match.review_status):
            continue
        opponent_id = match.club_b_id if match.club_a_id == club_id else match.club_a_id
        entry = opponents.setdefault(
            opponent_id,
            {
                "club_id": opponent_id,
                "name": opponent_id,
                "played": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "goals_for": 0,
                "goals_against": 0,
            },
        )
        entry["played"] = int(entry["played"]) + 1
        entry["goals_for"] = int(entry["goals_for"]) + (club_match.goals_for or 0)
        entry["goals_against"] = int(entry["goals_against"]) + (club_match.goals_against or 0)
        if club_match.is_win:
            entry["wins"] = int(entry["wins"]) + 1
        elif club_match.is_draw:
            entry["draws"] = int(entry["draws"]) + 1
        else:
            entry["losses"] = int(entry["losses"]) + 1

    if opponents:
        names = dict(
            session.query(Club.club_id, Club.name)
            .filter(Club.club_id.in_(list(opponents.keys())))
            .all()
        )
        for club_key, entry in opponents.items():
            entry["name"] = names.get(club_key) or club_key

    return sorted(opponents.values(), key=lambda item: (-int(item["played"]), str(item["name"])))


def stage_breakdown(rows: Iterable[tuple[ClubMatch, Match, MatchClassification]]) -> list[dict[str, object]]:
    """Campaign grouped by stage (falls back to 'Sem fase')."""
    groups: dict[str, dict[str, object]] = {}
    for club_match, match, classification in rows:
        if club_match.is_ignored or not is_counted(match.review_status):
            continue
        stage = (classification.stage or "").strip() or "Sem fase"
        entry = groups.setdefault(
            stage,
            {
                "stage": stage,
                "played": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "goals_for": 0,
                "goals_against": 0,
            },
        )
        entry["played"] = int(entry["played"]) + 1
        entry["goals_for"] = int(entry["goals_for"]) + (club_match.goals_for or 0)
        entry["goals_against"] = int(entry["goals_against"]) + (club_match.goals_against or 0)
        if club_match.is_win:
            entry["wins"] = int(entry["wins"]) + 1
        elif club_match.is_draw:
            entry["draws"] = int(entry["draws"]) + 1
        else:
            entry["losses"] = int(entry["losses"]) + 1

    for entry in groups.values():
        wins = int(entry["wins"])
        draws = int(entry["draws"])
        entry["points"] = wins * 3 + draws
    return sorted(groups.values(), key=lambda item: str(item["stage"]))


def match_row(
    club_match: ClubMatch,
    match: Match,
    names: dict[str, str],
    classification: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the presentation dict shared by the API and the dashboard."""
    return {
        "match_id": match.match_id,
        "played_at": match.played_at,
        "club_a_id": match.club_a_id,
        "club_b_id": match.club_b_id,
        "club_a_name": names.get(match.club_a_id, match.club_a_id),
        "club_b_name": names.get(match.club_b_id, match.club_b_id),
        "club_a_goals": match.club_a_goals,
        "club_b_goals": match.club_b_goals,
        "goals_for": club_match.goals_for,
        "goals_against": club_match.goals_against,
        "is_win": club_match.is_win,
        "is_draw": club_match.is_draw,
        "is_loss": club_match.is_loss,
        "is_ignored": club_match.is_ignored,
        "dnf": match.dnf,
        "match_type_code": match.match_type_code,
        "match_type_label": match_type_label(match.match_type_code),
        "championship": classification,
        "review": review_view(match),
    }


def championship_rows(
    session: Session,
    club_id: str,
    championship_id: int,
) -> list[tuple[ClubMatch, Match]]:
    """All matches of a club inside one championship, newest first."""
    return (
        session.query(ClubMatch, Match)
        .join(Match, Match.match_id == ClubMatch.match_id)
        .join(MatchClassification, MatchClassification.match_id == Match.match_id)
        .filter(
            ClubMatch.club_id == club_id,
            MatchClassification.championship_id == championship_id,
        )
        .order_by(Match.played_at.desc())
        .all()
    )


def stage_rows(
    session: Session,
    club_id: str,
    championship_id: int,
) -> list[tuple[ClubMatch, Match, MatchClassification]]:
    return (
        session.query(ClubMatch, Match, MatchClassification)
        .join(Match, Match.match_id == ClubMatch.match_id)
        .join(MatchClassification, MatchClassification.match_id == Match.match_id)
        .filter(
            ClubMatch.club_id == club_id,
            MatchClassification.championship_id == championship_id,
        )
        .all()
    )

