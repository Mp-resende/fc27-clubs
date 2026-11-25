from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.ea_public_cache import fetch_ea_public_club, search_ea_clubs_by_name

router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.get("/ea/search")
def search_public_ea_clubs(
    q: str = Query(..., min_length=2, description="Nome ou ID do clube na EA Sports"),
    platform: str = Query(default="common-gen5"),
) -> dict[str, object]:
    """Busca pública de clubes por nome ou ID oficial na EA Sports."""
    results = search_ea_clubs_by_name(query=q, platform=platform)
    return {
        "ok": True,
        "query": q,
        "count": len(results),
        "clubs": results,
    }


@router.get("/ea/clubs/{club_id}")
def get_public_ea_club(
    club_id: str,
    platform: str = Query(default="common-gen5"),
) -> dict[str, object]:
    """Public endpoint: fetch live club info from EA without requiring authentication or DB persistence."""
    data = fetch_ea_public_club(club_id=club_id.strip(), platform=platform)
    return {
        "ok": True,
        "club": data,
        "message": "Dados em tempo real obtidos da EA com cache de 10 minutos. Cadastre o clube para salvar histórico contínuo.",
    }
