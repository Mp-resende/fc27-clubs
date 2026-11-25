from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas.club import ClubInfo
from app.core.player_analytics import get_club_lineups
from app.db.models import Club

router = APIRouter(prefix="/api/v1", tags=["clubs"])


@router.get("/clubs", response_model=list[ClubInfo])
def list_clubs(db: Session = Depends(get_db)) -> list[Club]:
    return db.query(Club).order_by(Club.name).all()  # type: ignore[return-value]


@router.get("/clubs/{club_id}", response_model=ClubInfo)
def get_club(club_id: str, db: Session = Depends(get_db)) -> Club:
    club = db.query(Club).filter(Club.club_id == club_id).first()
    if club is None:
        raise HTTPException(status_code=404, detail=f"Club {club_id!r} not found")
    return club  # type: ignore[return-value]


@router.get("/clubs/{club_id}/lineups")
def get_club_frequent_lineups(
    club_id: str,
    min_matches: int = Query(default=5, ge=1, le=100),
    top_n: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Detector de Panelinhas: identifica os grupos de 5+ jogadores com mais jogos e vitórias juntos."""
    club = db.query(Club).filter(Club.club_id == club_id).first()
    if club is None:
        raise HTTPException(status_code=404, detail=f"Club {club_id!r} not found")

    lineups = get_club_lineups(db, club_id=club_id, min_matches=min_matches, top_n=top_n)
    return {
        "club_id": club_id,
        "club_name": club.name,
        "lineups": lineups,
    }
