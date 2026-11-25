"""Analytics API: EA season snapshot, derived summaries and H2H.

All endpoints return JSON and follow ``/api/v1``. EA season data (``overall``,
``members``) is always returned under a distinct key so clients can tell the
official season snapshot apart from the local match sample.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.config import Settings
from app.core import analytics
from app.core.analytics import MatchFilters
from app.db.models import (
    ClubMember,
    ClubMemberCareer,
    ClubOverallStats,
    Match,
)
from app.worker.ingestor import sync_club_profile

router = APIRouter(prefix="/api/v1", tags=["analytics"])


def _club_name_map(db: Session, club_ids: set[str], platform: str) -> dict[str, str]:
    from app.db.models import Club

    names: dict[str, str] = {}
    for club_id in club_ids:
        row = db.get(Club, {"club_id": club_id, "platform": platform})
        if row is not None:
            names[club_id] = row.name
    return names


def _filters_from_query(
    match_type: str | None,
    days: int | None,
    last_n: int | None,
    result: str | None,
    margin: str | None,
    opponent: str | None,
) -> MatchFilters:
    params: dict[str, Any] = {
        "tipo": match_type,
        "dias": days,
        "n": last_n,
        "resultado": result,
        "margem": margin,
        "adversario": opponent,
    }
    return MatchFilters.from_query(params)


# ─── EA season snapshot ──────────────────────────────────────────────────────


@router.get("/clubs/{club_id}/season/overall")
def club_season_overall(
    club_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """EA's whole-season club stats (official source, not the local sample)."""
    overall = db.get(
        ClubOverallStats, {"club_id": club_id, "platform": settings.PLATFORM}
    )
    if overall is None:
        raise HTTPException(status_code=404, detail="Season snapshot not synced yet")
    names = _club_name_map(db, {club_id}, settings.PLATFORM)
    return {
        "source": "ea",
        "official": analytics.official_summary(overall, names),
    }


@router.get("/clubs/{club_id}/season/members")
def club_season_members(
    club_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """EA member stats (season) and career totals, with local sample merge."""
    members = (
        db.query(ClubMember)
        .filter(
            ClubMember.club_id == club_id,
            ClubMember.platform == settings.PLATFORM,
        )
        .order_by(ClubMember.overall.desc())
        .all()
    )
    careers = (
        db.query(ClubMemberCareer)
        .filter(
            ClubMemberCareer.club_id == club_id,
            ClubMemberCareer.platform == settings.PLATFORM,
        )
        .all()
    )
    by_name = {career.name: career for career in careers}

    items = []
    for member in members:
        career = by_name.get(member.name)
        items.append(
            {
                "name": member.name,
                "player_key": member.player_key,
                "position": member.position,
                "overall": member.overall,
                "season": {
                    "games": member.games_played,
                    "goals": member.goals,
                    "assists": member.assists,
                    "rating_avg": member.rating_avg,
                    "win_rate": member.win_rate,
                    "motm": member.man_of_the_match,
                    "pass_success_rate": member.pass_success_rate,
                    "shot_success_rate": member.shot_success_rate,
                },
                "career": None
                if career is None
                else {
                    "games": career.games_played,
                    "goals": career.goals,
                    "assists": career.assists,
                    "rating_avg": career.rating_avg,
                    "motm": career.man_of_the_match,
                },
                "updated_at": member.updated_at,
            }
        )
    return {"source": "ea", "members": items, "count": len(items)}


@router.post("/clubs/{club_id}/season/sync")
def club_season_sync(
    club_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """On-demand refresh of the EA season snapshot for any club (incl. rivals)."""
    if not db.get(_club_model(), {"club_id": club_id, "platform": settings.PLATFORM}):
        raise HTTPException(status_code=404, detail="Club not registered")
    try:
        from app.worker.scheduler import _make_ea_client

        summary = sync_club_profile(
            club_id, db, _make_ea_client(), platform=settings.PLATFORM
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - network operation
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "synced", "summary": summary}


def _club_model() -> type:
    from app.db.models import Club

    return Club


# ─── Derived summaries over the local sample ────────────────────────────────


@router.get("/clubs/{club_id}/analytics/summary")
def club_analytics_summary(
    club_id: str,
    match_type: str | None = Query(default=None, description="league|playoff|friendly"),
    days: int | None = Query(default=None, ge=1),
    last_n: int | None = Query(default=None, ge=1),
    result: str | None = Query(default=None, description="win|draw|loss"),
    margin: str | None = Query(default=None, description="1|2|3+"),
    opponent: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Local-sample summary plus the EA season snapshot, side by side."""
    filters = _filters_from_query(match_type, days, last_n, result, margin, opponent)
    rows = analytics.filtered_matches(db, club_id, filters)
    names = _club_name_map(
        db,
        {club_id} | {m.club_a_id for _, m in rows} | {m.club_b_id for _, m in rows},
        settings.PLATFORM,
    )
    overall = db.get(
        ClubOverallStats, {"club_id": club_id, "platform": settings.PLATFORM}
    )
    return {
        "local": analytics.local_summary(rows),
        "official": analytics.official_summary(overall, names),
        "filters": filters.describe(),
        "goals_chart": (
            analytics.goals_chart(rows, club_id, names) if rows else None
        ),
    }


@router.get("/clubs/{club_id}/analytics/players")
def club_analytics_players(
    club_id: str,
    sort: str = Query(default="power", description="power|ga90|rating|goals|assists|minutes|name"),
    match_type: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1),
    last_n: int | None = Query(default=None, ge=1),
    result: str | None = Query(default=None),
    margin: str | None = Query(default=None),
    opponent: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    filters = _filters_from_query(match_type, days, last_n, result, margin, opponent)
    rows = analytics.filtered_matches(db, club_id, filters)
    players = analytics.player_aggregates(db, club_id, rows)
    return analytics.sort_players(players, sort)


@router.get("/clubs/{club_id}/analytics/team-profile")
def club_team_profile(
    club_id: str,
    match_type: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1),
    last_n: int | None = Query(default=None, ge=1),
    result: str | None = Query(default=None),
    margin: str | None = Query(default=None),
    opponent: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    filters = _filters_from_query(match_type, days, last_n, result, margin, opponent)
    rows = analytics.filtered_matches(db, club_id, filters)
    players = analytics.player_aggregates(db, club_id, rows)
    return analytics.team_profile(db, club_id, rows, players)


@router.get("/clubs/{club_id}/analytics/sessions")
def club_sessions(
    club_id: str,
    match_type: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1),
    last_n: int | None = Query(default=None, ge=1),
    result: str | None = Query(default=None),
    margin: str | None = Query(default=None),
    opponent: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    filters = _filters_from_query(match_type, days, last_n, result, margin, opponent)
    rows = analytics.filtered_matches(db, club_id, filters)
    return analytics.session_reports(db, club_id, rows)


@router.get("/clubs/{club_id}/analytics/pass-directions")
def club_pass_directions(
    club_id: str,
    limit: int = Query(default=12, le=40, ge=1),
    match_type: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1),
    last_n: int | None = Query(default=None, ge=1),
    result: str | None = Query(default=None),
    margin: str | None = Query(default=None),
    opponent: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    filters = _filters_from_query(match_type, days, last_n, result, margin, opponent)
    rows = analytics.filtered_matches(db, club_id, filters)
    players = analytics.player_aggregates(db, club_id, rows)
    directions = analytics.pass_direction_players(players, limit)
    return {
        "directions": directions,
        "note": "Event telemetry from EA is only available for league/playoff matches.",
    }


# ─── H2H ─────────────────────────────────────────────────────────────────────


@router.get("/clubs/{club_id}/h2h/{opponent_id}")
def club_h2h(
    club_id: str,
    opponent_id: str,
    match_type: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=1),
    last_n: int | None = Query(default=None, ge=1),
    result: str | None = Query(default=None),
    margin: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    filters = _filters_from_query(match_type, days, last_n, result, margin, None)
    rows = analytics.filtered_matches(db, club_id, filters)
    names = _club_name_map(db, {club_id, opponent_id}, settings.PLATFORM)
    summary = analytics.h2h_summary(rows, club_id, opponent_id)
    opponent_overall = db.get(
        ClubOverallStats,
        {"club_id": opponent_id, "platform": settings.PLATFORM},
    )
    return {
        "summary": summary,
        "opponent_name": names.get(opponent_id, opponent_id),
        "opponent_official": analytics.official_summary(
            opponent_overall,
            _club_name_map(db, {opponent_id}, settings.PLATFORM),
        ),
    }


@router.get("/clubs/{club_id}/matches/{match_id}/analytics")
def match_analytics_endpoint(
    club_id: str,
    match_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Team comparison + per-player event metrics for one match."""
    match = db.get(Match, match_id)
    if match is None or club_id not in {match.club_a_id, match.club_b_id}:
        raise HTTPException(status_code=404, detail="Match not found for this club")
    return analytics.match_analytics(
        db, match_id, [match.club_a_id, match.club_b_id]
    )
