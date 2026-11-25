"""Data export and maintenance endpoints.

The accumulated SQLite database is the permanent record of the history. These
endpoints make it portable (CSV) and let an operator force a backup.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.config import Settings
from app.db.models import Club, ClubMatch, Match, PlayerMatchStats
from app.worker.backup import create_backup

router = APIRouter(prefix="/api/v1", tags=["export"])

_CSV_MEDIA_TYPE = "text/csv; charset=utf-8"


def _csv_response(rows: list[list[object]], header: list[str], filename: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        content=buffer.getvalue(),
        media_type=_CSV_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _require_club(club_id: str, settings: Settings) -> None:
    if club_id != settings.CLUB_ID:
        raise HTTPException(status_code=403, detail="Export only allowed for configured club")


@router.get("/clubs/{club_id}/export/matches.csv")
def export_matches_csv(
    club_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Export the full accumulated match history for a club as CSV."""
    _require_club(club_id, settings)

    rows = (
        db.query(ClubMatch, Match)
        .join(Match, Match.match_id == ClubMatch.match_id)
        .filter(ClubMatch.club_id == club_id)
        .order_by(Match.played_at.desc())
        .all()
    )
    names = {
        cid: name
        for cid, name in db.query(Club.club_id, Club.name).all()
    }

    data = [
        [
            m.match_id,
            m.played_at,
            m.club_a_id,
            names.get(m.club_a_id, ""),
            m.club_a_goals,
            m.club_b_id,
            names.get(m.club_b_id, ""),
            m.club_b_goals,
            cm.goals_for,
            cm.goals_against,
            "V" if cm.is_win else ("E" if cm.is_draw else "D"),
            m.match_type_code,
            m.dnf,
            m.review_status,
            m.result_source,
            cm.is_ignored,
        ]
        for cm, m in rows
    ]
    header = [
        "match_id",
        "played_at",
        "home_id",
        "home_name",
        "home_goals",
        "away_id",
        "away_name",
        "away_goals",
        "goals_for",
        "goals_against",
        "result",
        "match_type",
        "dnf",
        "review_status",
        "result_source",
        "is_ignored",
    ]
    return _csv_response(data, header, f"fc27_{club_id}_matches.csv")


@router.get("/clubs/{club_id}/export/players.csv")
def export_players_csv(
    club_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Export every player-match row for a club as CSV."""
    _require_club(club_id, settings)

    rows = (
        db.query(PlayerMatchStats, Match)
        .join(Match, Match.match_id == PlayerMatchStats.match_id)
        .filter(PlayerMatchStats.club_id == club_id)
        .order_by(Match.played_at.desc(), PlayerMatchStats.playername.asc())
        .all()
    )
    data = [
        [
            ps.match_id,
            m.played_at,
            ps.club_id,
            ps.player_key,
            ps.playername,
            ps.position or "",
            ps.rating if ps.rating is not None else "",
            ps.seconds_played or 0,
            ps.goals or 0,
            ps.assists or 0,
            ps.shots or 0,
            ps.passes_made or 0,
            ps.pass_attempts or 0,
            ps.tackles_made or 0,
            ps.tackle_attempts or 0,
            ps.saves or 0,
            ps.goals_conceded or 0,
            ps.is_mom or 0,
            ps.red_cards or 0,
            ps.archetype_id or "",
        ]
        for ps, m in rows
    ]
    header = [
        "match_id",
        "played_at",
        "club_id",
        "player_key",
        "playername",
        "position",
        "rating",
        "seconds_played",
        "goals",
        "assists",
        "shots",
        "passes_made",
        "pass_attempts",
        "tackles_made",
        "tackle_attempts",
        "saves",
        "goals_conceded",
        "is_mom",
        "red_cards",
        "archetype_id",
    ]
    return _csv_response(data, header, f"fc27_{club_id}_players.csv")


@router.post("/maintenance/backup")
def force_backup() -> dict[str, object]:
    """Create an on-demand database snapshot."""
    path = create_backup()
    if path is None:
        raise HTTPException(status_code=500, detail="Backup failed")
    return {"created": True, "path": str(path)}
