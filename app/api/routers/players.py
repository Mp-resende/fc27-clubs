from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.player_analytics import (
    compare_players_profiles,
    get_player_full_profile,
    get_team_of_the_week,
    search_suggest_entities,
)
from app.core.review import COUNTED_STATUSES
from app.db.models import ClubMatch, Match, Player, PlayerMatchStats, PlayerName

router = APIRouter(prefix="/api/v1", tags=["players"])


@router.get("/search/suggest")
def suggest_entities(
    q: str = Query(..., min_length=2, description="Termo de pesquisa (gamertag, nome antigo, EA ID ou clube)"),
    type: str = Query(default="all", pattern="^(all|players|clubs)$"),
    limit: int = Query(default=15, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Endpoint de autocomplete estilo SpotXI (/api/stats/suggest).
    
    Permite busca instantânea de jogadores (por gamertag atual, nomes anteriores ou EA ID)
    e de clubes registrados na base de dados.
    """
    return search_suggest_entities(db, query_text=q, search_type=type, limit=limit)


@router.get("/players/compare")
def compare_players(
    vs: str = Query(..., description="Chaves de jogadores separadas por vírgula (ex: k1,k2)"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Comparador de jogadores head-to-head com cálculo de deltas (+/-) estilo SpotXI."""
    keys = [k.strip() for k in vs.split(",") if k.strip()]
    if len(keys) < 2:
        raise HTTPException(
            status_code=400,
            detail="Informe pelo menos 2 chaves de jogadores no parâmetro 'vs' (ex: ?vs=p1,p2).",
        )
    return compare_players_profiles(db, keys)


@router.get("/analytics/totw")
def team_of_the_week(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Equipe da Semana (TOTW) em esquema 3-1-4-2 com volante de contenção e Craque da Semana."""
    return get_team_of_the_week(db, days=days)


@router.get("/players/{player_key}/profile")
def get_player_profile(
    player_key: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Retorna a ficha analítica global do jogador estilo SpotXI.
    
    Consolida histórico de clubes, posição principal (moda estatística),
    score de desempenho (0 a 100), telemetria detalhada (passes, desarmes, finalizações)
    e gráfico de forma recente dos últimos jogos.
    """
    profile = get_player_full_profile(db, player_key)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Player {player_key!r} not found")
    return profile


@router.get("/clubs/{club_id}/players")
def list_club_players(
    club_id: str,
    min_seconds_played: int = Query(default=1, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    rows = (
        db.query(
            PlayerMatchStats.player_key,
            PlayerMatchStats.playername,
            func.count(PlayerMatchStats.match_id).label("matches"),
            func.avg(PlayerMatchStats.rating).label("avg_rating"),
            func.sum(PlayerMatchStats.goals).label("total_goals"),
            func.sum(PlayerMatchStats.assists).label("total_assists"),
            func.sum(PlayerMatchStats.seconds_played).label("total_seconds"),
        )
        .join(Match, Match.match_id == PlayerMatchStats.match_id)
        .filter(
            PlayerMatchStats.club_id == club_id,
            PlayerMatchStats.seconds_played >= min_seconds_played,
            Match.review_status.in_(COUNTED_STATUSES),
        )
        .group_by(PlayerMatchStats.player_key, PlayerMatchStats.playername)
        .order_by(func.avg(PlayerMatchStats.rating).desc())
        .all()
    )

    players = [
        {
            "player_key": r.player_key,
            "playername": r.playername,
            "matches": r.matches,
            "avg_rating": round(r.avg_rating, 2) if r.avg_rating is not None else None,
            "total_goals": r.total_goals or 0,
            "total_assists": r.total_assists or 0,
            "total_seconds_played": r.total_seconds or 0,
        }
        for r in rows
    ]
    return {"club_id": club_id, "players": players}


@router.get("/clubs/{club_id}/players/{player_key}")
def get_player(
    club_id: str,
    player_key: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    player = db.get(Player, player_key)
    if player is None:
        raise HTTPException(status_code=404, detail=f"Player {player_key!r} not found")

    name_rows = (
        db.query(PlayerName)
        .filter(PlayerName.player_key == player_key)
        .all()
    )
    names = [row.playername for row in name_rows]

    history = (
        db.query(PlayerMatchStats)
        .join(Match, Match.match_id == PlayerMatchStats.match_id)
        .filter(
            PlayerMatchStats.player_key == player_key,
            PlayerMatchStats.club_id == club_id,
            Match.review_status.in_(COUNTED_STATUSES),
        )
        .order_by(Match.played_at.desc())
        .all()
    )

    matches_out = [
        {
            "match_id": ps.match_id,
            "playername": ps.playername,
            "position": ps.position,
            "rating": ps.rating,
            "seconds_played": ps.seconds_played,
            "goals": ps.goals,
            "assists": ps.assists,
            "shots": ps.shots,
            "saves": ps.saves,
            "is_mom": ps.is_mom,
        }
        for ps in history
    ]

    return {
        "player_key": player_key,
        "ea_player_id": player.ea_player_id,
        "platform": player.platform,
        "names": names,
        "first_seen_at": player.first_seen_at,
        "matches": matches_out,
    }
