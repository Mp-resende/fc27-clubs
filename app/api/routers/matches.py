from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas.match import MatchDetail, MatchSummary
from app.api.schemas.player import PlayerStats
from app.core.championships import classification_map
from app.core.review import is_counted, review_view
from app.db.models import ClubMatch, Match, PlayerMatchMetrics, PlayerMatchStats

router = APIRouter(prefix="/api/v1", tags=["matches"])


@router.get("/clubs/{club_id}/matches")
def list_club_matches(
    club_id: str,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    result: str | None = Query(default=None, description="win|draw|loss"),
    opponent_club_id: str | None = Query(default=None),
    review_status: str | None = Query(
        default=None, description="valid|needs_review|ignored|corrected"
    ),
    limit: int = Query(default=20, le=100, ge=1),
    cursor: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    q = (
        db.query(ClubMatch, Match)
        .join(Match, Match.match_id == ClubMatch.match_id)
        .filter(ClubMatch.club_id == club_id)
    )

    if from_date:
        q = q.filter(Match.played_at >= from_date)
    if to_date:
        q = q.filter(Match.played_at <= to_date)
    if result == "win":
        q = q.filter(ClubMatch.is_win == 1)
    elif result == "draw":
        q = q.filter(ClubMatch.is_draw == 1)
    elif result == "loss":
        q = q.filter(ClubMatch.is_loss == 1)
    if opponent_club_id:
        q = q.filter(
            (Match.club_a_id == opponent_club_id) | (Match.club_b_id == opponent_club_id)
        )
    if review_status:
        q = q.filter(Match.review_status == review_status)
    if cursor:
        q = q.filter(Match.played_at < cursor)

    q = q.order_by(Match.played_at.desc()).limit(limit + 1)
    rows = q.all()

    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor: str | None = None
    if has_more and rows:
        next_cursor = rows[-1][1].played_at

    items = []
    classifications = classification_map(db, [m.match_id for _, m in rows])
    for cm, m in rows:
        items.append(
            {
                "match_id": m.match_id,
                "played_at": m.played_at,
                "club_a_id": m.club_a_id,
                "club_b_id": m.club_b_id,
                "club_a_goals": m.club_a_goals,
                "club_b_goals": m.club_b_goals,
                "goals_for": cm.goals_for,
                "goals_against": cm.goals_against,
                "is_win": cm.is_win,
                "is_draw": cm.is_draw,
                "is_loss": cm.is_loss,
                "dnf": m.dnf,
                "review_status": m.review_status or "valid",
                "review_reason": m.review_reason,
                "review_note": m.review_note,
                "result_source": m.result_source or "ea",
                "ea_club_a_goals": m.ea_club_a_goals,
                "ea_club_b_goals": m.ea_club_b_goals,
                "is_counted": is_counted(m.review_status),
                "review": review_view(m),
                "classification": classifications.get(m.match_id),
            }
        )

    return {"items": items, "next_cursor": next_cursor}


@router.get("/matches/{match_id}", response_model=MatchDetail)
def get_match(match_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id!r} not found")

    player_rows = (
        db.query(PlayerMatchStats)
        .filter(PlayerMatchStats.match_id == match_id)
        .all()
    )

    players_out = []
    for ps in player_rows:
        metrics: dict[str, object] = {}
        m = db.get(PlayerMatchMetrics, (ps.match_id, ps.player_key))
        if m is not None:
            metrics = {
                col.name: getattr(m, col.name)
                for col in m.__table__.columns
                if col.name not in ("match_id", "player_key", "metrics_version")
            }
        players_out.append(
            PlayerStats(
                player_key=ps.player_key,
                club_id=ps.club_id,
                playername=ps.playername,
                position=ps.position,
                rating=ps.rating,
                seconds_played=ps.seconds_played,
                goals=ps.goals or 0,
                assists=ps.assists or 0,
                shots=ps.shots or 0,
                passes_made=ps.passes_made or 0,
                saves=ps.saves or 0,
                is_mom=ps.is_mom or 0,
                metrics=metrics,
            ).model_dump()
        )

    return {
        "match_id": match.match_id,
        "played_at": match.played_at,
        "platform": match.platform,
        "match_type_code": match.match_type_code,
        "club_a_id": match.club_a_id,
        "club_b_id": match.club_b_id,
        "club_a_goals": match.club_a_goals,
        "club_b_goals": match.club_b_goals,
        "dnf": match.dnf,
        "winner_by_dnf": match.winner_by_dnf,
        "season_id": match.season_id,
        "parser_version": match.parser_version,
        "review_status": match.review_status or "valid",
        "review_reason": match.review_reason,
        "review_note": match.review_note,
        "result_source": match.result_source or "ea",
        "ea_club_a_goals": match.ea_club_a_goals,
        "ea_club_b_goals": match.ea_club_b_goals,
        "is_counted": is_counted(match.review_status),
        "review": review_view(match),
        "classification": classification_map(db, [match_id]).get(match_id),
        "players": players_out,
    }


@router.get("/matches/{match_id}/raw")
def get_match_raw(match_id: str, db: Session = Depends(get_db)) -> object:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Match {match_id!r} not found")
    return json.loads(match.raw_payload)
