"""Link EA member names to local ``player_key`` values.

EA's ``members/stats`` endpoint has no player id, so the only bridge to the
match payloads is the member name. This module normalises names, resolves the
unambiguous ones automatically and keeps a manual alias table for the rest.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import (
    ClubMember,
    ClubMemberCareer,
    PlayerAlias,
    PlayerMatchStats,
    PlayerName,
)

_IGNORED_NAMES = {"-", "", "unknown", "null", "none"}


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_name(value: str | None) -> str:
    """Case/accent/punctuation-insensitive key used to match names."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", " ", ascii_only.casefold())
    return " ".join(cleaned.split())


def is_usable_name(value: str | None) -> bool:
    return normalize_name(value) not in _IGNORED_NAMES


def local_name_index(db: Session, club_id: str) -> dict[str, set[str]]:
    """Map normalised match name -> set of player_key seen for this club.

    A name that maps to more than one key is considered ambiguous and must be
    resolved manually through an alias.
    """
    rows = (
        db.query(PlayerName.playername, PlayerName.player_key)
        .join(PlayerMatchStats, PlayerMatchStats.player_key == PlayerName.player_key)
        .filter(PlayerMatchStats.club_id == club_id)
        .distinct()
        .all()
    )
    index: dict[str, set[str]] = {}
    for playername, player_key in rows:
        key = normalize_name(playername)
        if not is_usable_name(playername):
            continue
        index.setdefault(key, set()).add(player_key)
    return index


def alias_map(db: Session) -> dict[str, str]:
    return {row.alias: row.player_key for row in db.query(PlayerAlias).all()}


def _unique(index: dict[str, set[str]], name: str) -> str | None:
    keys = index.get(normalize_name(name))
    if keys and len(keys) == 1:
        return next(iter(keys))
    return None


def link_members(db: Session, club_id: str, platform: str | None = None) -> dict[str, int]:
    """Attach player_key to every member/career row that can be resolved.

    Aliases win over automatic matches. Returns counts for the status page.
    """
    index = local_name_index(db, club_id)
    aliases = alias_map(db)

    def resolve(name: str | None) -> str | None:
        if not name:
            return None
        alias = aliases.get(normalize_name(name))
        if alias:
            return alias
        return _unique(index, name)

    linked = 0
    unresolved = 0

    member_query = db.query(ClubMember).filter(ClubMember.club_id == club_id)
    if platform:
        member_query = member_query.filter(ClubMember.platform == platform)
    for member in member_query.all():
        key = resolve(member.name)
        if key:
            member.player_key = key
            linked += 1
        elif not member.player_key:
            unresolved += 1

    career_query = db.query(ClubMemberCareer).filter(ClubMemberCareer.club_id == club_id)
    if platform:
        career_query = career_query.filter(ClubMemberCareer.platform == platform)
    for career in career_query.all():
        key = resolve(career.name)
        if key:
            career.player_key = key
        elif not career.player_key:
            unresolved += 1

    db.flush()
    return {"linked": linked, "unresolved": unresolved, "indexed_names": len(index)}


def link_member(
    db: Session,
    club_id: str,
    platform: str,
    member_name: str,
    player_key: str,
) -> None:
    """Manually link one member (and its career row) to a player_key."""
    normalized = normalize_name(member_name)
    if not normalized:
        raise ValueError("Nome do membro inválido")

    alias = db.get(PlayerAlias, normalized)
    if alias is None:
        alias = PlayerAlias(
            alias=normalized,
            display_alias=member_name.strip(),
            player_key=player_key,
            created_at=now_iso(),
        )
        db.add(alias)
    else:
        alias.player_key = player_key
        alias.display_alias = member_name.strip()

    member = db.get(
        ClubMember, {"club_id": club_id, "platform": platform, "name": member_name}
    )
    if member is not None:
        member.player_key = player_key

    career = db.get(
        ClubMemberCareer,
        {"club_id": club_id, "platform": platform, "name": member_name},
    )
    if career is not None:
        career.player_key = player_key

    db.flush()
