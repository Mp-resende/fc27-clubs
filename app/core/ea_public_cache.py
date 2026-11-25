from __future__ import annotations

from datetime import datetime, timezone, timedelta
import threading
from typing import Any

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

# In-memory thread-safe cache: {f"{platform}:{club_id}": (expires_at, data)}
_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}
_LOCK = threading.Lock()
CACHE_TTL_MINUTES = 10


def _get_ea_client() -> object:
    try:
        from fc_clubs_api import FC27API  # type: ignore[import]
        return FC27API(timeout=settings.EA_TIMEOUT_SECONDS, output="raw")
    except ImportError:
        return None


def fetch_ea_public_club(club_id: str, platform: str = "common-gen5") -> dict[str, Any]:
    """Fetch live club info and recent matches from EA, cached for 10 minutes.
    
    Does NOT persist to the permanent database.
    """
    key = f"{platform}:{club_id}"
    now = datetime.now(timezone.utc)

    with _LOCK:
        if key in _CACHE:
            expires_at, cached_data = _CACHE[key]
            if now < expires_at:
                return cached_data

    # Fetch from EA
    client = _get_ea_client()
    data: dict[str, Any] = {
        "club_id": club_id,
        "platform": platform,
        "name": f"Clube {club_id}",
        "matches": [],
        "overall": None,
        "source": "ea_live",
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if client:
        try:
            # Query recent matches
            if hasattr(client, "get_club_matches"):
                matches = client.get_club_matches(club_id=club_id, match_type="friendlyMatch", count=10)
                data["matches"] = matches or []
                if matches and isinstance(matches, list) and len(matches) > 0:
                    clubs_dict = matches[0].get("clubs", {})
                    if club_id in clubs_dict:
                        data["name"] = clubs_dict[club_id].get("details", {}).get("name", data["name"])
        except Exception as exc:
            log.warning("ea_public_cache.fetch_failed", club_id=club_id, error=str(exc))

    with _LOCK:
        _CACHE[key] = (now + timedelta(minutes=CACHE_TTL_MINUTES), data)

    return data


def search_ea_clubs_by_name(query: str, platform: str = "common-gen5") -> list[dict[str, Any]]:
    """Search for Pro Clubs on EA by name or ID.
    
    Returns a standardized list of clubs matching the search query.
    Cached for CACHE_TTL_MINUTES in memory.
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    cache_key = f"search:{platform}:{clean_query.lower()}"
    now = datetime.now(timezone.utc)

    with _LOCK:
        if cache_key in _CACHE:
            expires_at, cached_list = _CACHE[cache_key]
            if now < expires_at:
                return cached_list  # type: ignore[return-value]

    client = _get_ea_client()
    results: list[dict[str, Any]] = []

    if client:
        try:
            # 1. Se o usuário digitou texto, busca por nome na EA
            raw_clubs = []
            if hasattr(client, "search_club_by_name"):
                try:
                    raw_clubs = client.search_club_by_name(clean_query) or []
                except Exception as exc:
                    log.warning("ea_public_cache.search_name_failed", query=clean_query, error=str(exc))

            # 2. Se a busca por nome retornou resultados, formata cada um
            seen_ids = set()
            if isinstance(raw_clubs, list):
                for item in raw_clubs:
                    if not isinstance(item, dict):
                        continue
                    cid = str(item.get("clubId") or item.get("clubInfo", {}).get("clubId") or "").strip()
                    if not cid or cid in seen_ids:
                        continue
                    seen_ids.add(cid)

                    cinfo = item.get("clubInfo", {})
                    cname = item.get("clubName") or cinfo.get("name") or f"Clube {cid}"
                    cdiv = item.get("currentDivision") or "Divisão Aberta"
                    
                    # Identificar Asset ID do Escudo
                    custom_kit = cinfo.get("customKit", {})
                    crest_asset = (
                        custom_kit.get("crestAssetId")
                        or cinfo.get("teamId")
                    )
                    crest_url = None
                    if crest_asset and str(crest_asset) != "0":
                        crest_url = (
                            "https://eafc24.content.easports.com/fifa/fltOnlineAssets/"
                            "24B23FDE-7835-41C2-87A2-F453DFDB2E82/2024/fcweb/crests/256x256/"
                            f"l{crest_asset}.png"
                        )

                    results.append({
                        "club_id": cid,
                        "name": cname,
                        "platform": item.get("platform", platform),
                        "current_division": cdiv,
                        "wins": item.get("wins", 0),
                        "losses": item.get("losses", 0),
                        "ties": item.get("ties", 0),
                        "games_played": item.get("gamesPlayed", 0),
                        "crest_url": crest_url,
                    })

            # 3. Se a query for numérica e ainda não estiver nos resultados, tenta busca direta por ID
            if clean_query.isdigit() and clean_query not in seen_ids:
                try:
                    club_data = fetch_ea_public_club(clean_query, platform)
                    if club_data:
                        results.insert(0, {
                            "club_id": clean_query,
                            "name": club_data.get("name") or f"Clube {clean_query}",
                            "platform": platform,
                            "current_division": "EA Live",
                            "wins": "-",
                            "losses": "-",
                            "ties": "-",
                            "games_played": len(club_data.get("matches", [])),
                            "crest_url": None,
                        })
                except Exception as exc:
                    log.warning("ea_public_cache.direct_id_fallback_failed", club_id=clean_query, error=str(exc))

        except Exception as exc:
            log.warning("ea_public_cache.search_failed", query=clean_query, error=str(exc))

    with _LOCK:
        _CACHE[cache_key] = (now + timedelta(minutes=CACHE_TTL_MINUTES), results)

    return results
