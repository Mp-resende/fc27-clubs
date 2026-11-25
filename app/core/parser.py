from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.core.member_link import is_usable_name


def to_int(val: object) -> int | None:
    """Safely coerce EA string/int to int, returning None on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def to_float(val: object) -> float | None:
    """Safely coerce EA string/float to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def sha256_hex(s: str) -> str:
    """Return the SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# Fields that change between identical EA responses (e.g. "2 weeks ago").
# They are ignored when hashing so the same match always maps to one snapshot.
_VOLATILE_KEYS = frozenset({"timeAgo"})


def canonical_payload_json(raw: dict[str, object]) -> str:
    """Canonical JSON used for stable payload hashing."""
    stable = {k: v for k, v in raw.items() if k not in _VOLATILE_KEYS}
    return json.dumps(stable, separators=(",", ":"), sort_keys=True)


def _iso_from_timestamp(ts: object) -> str:
    """Convert a Unix timestamp (int/str) to UTC ISO-8601 string."""
    unix = to_int(ts) or 0
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_match(raw: dict[str, object], parser_version: str) -> dict[str, object]:
    """Extract normalised fields from a raw EA match payload dict.

    Real EA payload shape (validated against common-gen5, FC 27):
    - match-level fields (matchType, season_id, gameNumber) live inside each
      entry of ``clubs``, not at the top level.
    """
    match_id: str = str(raw.get("matchId", ""))
    played_at: str = _iso_from_timestamp(raw.get("timestamp"))

    clubs: dict[str, object] = raw.get("clubs", {})  # type: ignore[assignment]
    club_keys = sorted(clubs.keys(), key=lambda k: int(k))
    club_a_id = club_keys[0] if len(club_keys) > 0 else ""
    club_b_id = club_keys[1] if len(club_keys) > 1 else ""

    # Any club entry carries the match-level metadata; prefer club_a
    reference: dict[str, object] = {}
    for cid in club_keys:
        candidate = clubs.get(cid)
        if isinstance(candidate, dict):
            reference = candidate
            break

    match_type_code = str(reference.get("matchType", "") or raw.get("matchType", "") or "")
    season_id = (
        str(reference["season_id"])
        if reference.get("season_id") not in (None, "")
        else (str(raw["seasonId"]) if raw.get("seasonId") is not None else None)
    )
    game_number = (
        str(reference["gameNumber"])
        if reference.get("gameNumber") not in (None, "")
        else (str(raw["gameNumber"]) if raw.get("gameNumber") is not None else None)
    )

    def _goals(club_id: str) -> int:
        club_data = clubs.get(club_id, {})
        assert isinstance(club_data, dict)
        return to_int(club_data.get("goals")) or 0

    club_a_goals = _goals(club_a_id)
    club_b_goals = _goals(club_b_id)

    # DNF detection — present in any club's winnerByDnf field
    winner_by_dnf: str | None = None
    dnf = 0
    for cid, cdata in clubs.items():
        assert isinstance(cdata, dict)
        wbd = cdata.get("winnerByDnf")
        if wbd is not None and str(wbd) not in ("0", "", "None"):
            dnf = 1
            winner_by_dnf = str(wbd)
            break

    raw_payload = json.dumps(raw, separators=(",", ":"), sort_keys=True)
    payload_hash = sha256_hex(canonical_payload_json(raw))

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "match_id": match_id,
        "played_at": played_at,
        "match_type_code": match_type_code,
        "season_id": season_id,
        "game_number": game_number,
        "club_a_id": club_a_id,
        "club_b_id": club_b_id,
        "club_a_goals": club_a_goals,
        "club_b_goals": club_b_goals,
        "dnf": dnf,
        "winner_by_dnf": winner_by_dnf,
        "raw_payload": raw_payload,
        "payload_hash": payload_hash,
        "parser_version": parser_version,
        "ingested_at": now,
        "first_seen_at": now,
        "last_seen_at": now,
    }


def parse_player(ea_id: str, club_id: str, match_id: str, p: dict[str, object]) -> dict[str, object]:
    """Extract normalised player fields from a raw EA player dict.

    Real EA field names (validated): ``pos`` (not ``position``),
    ``archetypeid`` (all lowercase), ``secondsPlayed``, ``gameTime``.
    """
    playername = str(p.get("playername", ""))
    position_raw = p.get("pos") or p.get("position")
    position = str(position_raw) if position_raw not in (None, "") else None
    seconds_played = to_int(p.get("secondsPlayed"))
    game_time = to_int(p.get("gameTime"))

    # rating is NULL when player did not play (secondsPlayed == 0)
    raw_rating = to_float(p.get("rating"))
    rating: float | None = raw_rating if (seconds_played or 0) > 0 else None

    # player_key is the EA player id scoped to the platform
    player_key = ea_id

    archetype_raw = p.get("archetypeid") or p.get("archetypeId")

    raw_player = json.dumps(p, separators=(",", ":"), sort_keys=True)

    return {
        "match_id": match_id,
        "club_id": club_id,
        "player_key": player_key,
        "playername": playername,
        "position": position,
        "rating": rating,
        "seconds_played": seconds_played,
        "game_time": game_time,
        "goals": to_int(p.get("goals")) or 0,
        "assists": to_int(p.get("assists")) or 0,
        "shots": to_int(p.get("shots")) or 0,
        "passes_made": to_int(p.get("passesmade")) or 0,
        "pass_attempts": to_int(p.get("passattempts")) or 0,
        "tackles_made": to_int(p.get("tacklesmade")) or 0,
        "tackle_attempts": to_int(p.get("tackleattempts")) or 0,
        "saves": to_int(p.get("saves")) or 0,
        "goals_conceded": to_int(p.get("goalsconceded")) or 0,
        "clean_sheets_any": to_int(p.get("cleansheetsany")) or 0,
        "clean_sheets_def": to_int(p.get("cleansheetsdef")) or 0,
        "clean_sheets_gk": to_int(p.get("cleansheetsgk")) or 0,
        "is_mom": to_int(p.get("mom")) or 0,
        "red_cards": to_int(p.get("redcards")) or 0,
        "pro_overall": to_int(p.get("prooverall") or p.get("overall")),
        "archetype_id": str(archetype_raw) if archetype_raw not in (None, "") else None,
        "raw_player": raw_player,
    }


def _to_str(val: object) -> str | None:
    if val is None:
        return None
    text = str(val).strip()
    return text or None


def parse_overall_stats(club_id: str, raw: dict[str, object], platform: str) -> dict[str, object]:
    """Normalise one row of EA's ``clubs/overallStats`` response.

    ``lastMatch0..9`` uses EA's own result codes: 1 = win, 2 = loss,
    3 = draw, -1 = no match (validated against clubs/matches results).
    """
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    division_finishes: list[dict[str, int]] = []
    for key, value in raw.items():
        if not str(key).startswith("finishesInDivision"):
            continue
        digits = "".join(ch for ch in str(key) if ch.isdigit())
        if len(digits) < 2:
            continue
        count = to_int(value) or 0
        if count <= 0:
            continue
        division_finishes.append(
            {"division": int(digits[0]), "group": int(digits[1]), "count": count}
        )
    division_finishes.sort(key=lambda item: (item["division"], item["group"]))

    last_results: list[dict[str, object]] = []
    for index in range(10):
        result = to_int(raw.get(f"lastMatch{index}"))
        opponent = _to_str(raw.get(f"lastOpponent{index}"))
        if result is None or result < 0:
            continue
        last_results.append({"result": result, "opponent": opponent})

    return {
        "club_id": str(raw.get("clubId") or club_id),
        "platform": platform,
        "games_played": to_int(raw.get("gamesPlayed")),
        "wins": to_int(raw.get("wins")),
        "ties": to_int(raw.get("ties")),
        "losses": to_int(raw.get("losses")),
        "goals": to_int(raw.get("goals")),
        "goals_against": to_int(raw.get("goalsAgainst")),
        "promotions": to_int(raw.get("promotions")),
        "relegations": to_int(raw.get("relegations")),
        "best_division": to_int(raw.get("bestDivision")),
        "best_finish_group": to_int(raw.get("bestFinishGroup")),
        "skill_rating": to_int(raw.get("skillRating")),
        "reputation_tier": to_int(raw.get("reputationtier")),
        "win_streak": to_int(raw.get("wstreak")),
        "unbeaten_streak": to_int(raw.get("unbeatenstreak")),
        "league_appearances": to_int(raw.get("leagueAppearances")),
        "playoff_games": to_int(raw.get("gamesPlayedPlayoff")),
        "division_finishes": json.dumps(division_finishes, separators=(",", ":")),
        "last_results": json.dumps(last_results, separators=(",", ":")),
        "raw_json": json.dumps(raw, separators=(",", ":"), sort_keys=True),
        "updated_at": now,
    }


def _member_rows(payload: object) -> list[dict[str, object]]:
    """EA returns ``{"members": [...], "positionCount": {...}}``."""
    if isinstance(payload, dict):
        rows = payload.get("members")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _usable_member_rows(payload: object) -> list[tuple[str, dict[str, object]]]:
    """Valid member rows with a real (non placeholder) name."""
    out: list[tuple[str, dict[str, object]]] = []
    for row in _member_rows(payload):
        name = _to_str(row.get("name"))
        if not name or not is_usable_name(name):
            continue
        out.append((name, row))
    return out


def parse_member_stats(
    club_id: str, payload: object, platform: str
) -> list[dict[str, object]]:
    """Normalise the current-season member list.

    Placeholder names (``-``, blanks) are dropped: EA uses them for
    unregistered roster slots and they can never be linked.
    """
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[dict[str, object]] = []
    for name, row in _usable_member_rows(payload):
        prev_goals = [to_int(row.get("prevGoals")) or 0]
        for index in range(1, 11):
            prev_goals.append(to_int(row.get(f"prevGoals{index}")) or 0)
        out.append(
            {
                "club_id": club_id,
                "platform": platform,
                "name": name,
                "pro_name": _to_str(row.get("proName")),
                "position": _to_str(row.get("favoritePosition")),
                "pro_pos": to_int(row.get("proPos")),
                "overall": to_int(row.get("proOverall")),
                "games_played": to_int(row.get("gamesPlayed")),
                "win_rate": to_int(row.get("winRate")),
                "goals": to_int(row.get("goals")),
                "assists": to_int(row.get("assists")),
                "passes_made": to_int(row.get("passesMade")),
                "pass_success_rate": to_int(row.get("passSuccessRate")),
                "shot_success_rate": to_int(row.get("shotSuccessRate")),
                "tackle_success_rate": to_int(row.get("tackleSuccessRate")),
                "tackles_made": to_int(row.get("tacklesMade")),
                "rating_avg": to_float(row.get("ratingAve")),
                "man_of_the_match": to_int(row.get("manOfTheMatch")),
                "red_cards": to_int(row.get("redCards")),
                "clean_sheets_def": to_int(row.get("cleanSheetsDef")),
                "clean_sheets_gk": to_int(row.get("cleanSheetsGK")),
                "height": to_int(row.get("proHeight")),
                "nationality": _to_str(row.get("proNationality")),
                "prev_goals": json.dumps(prev_goals, separators=(",", ":")),
                "raw_json": json.dumps(row, separators=(",", ":"), sort_keys=True),
                "updated_at": now,
            }
        )
    return out


def parse_member_career(
    club_id: str, payload: object, platform: str
) -> list[dict[str, object]]:
    """Normalise the career totals list."""
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[dict[str, object]] = []
    for name, row in _usable_member_rows(payload):
        out.append(
            {
                "club_id": club_id,
                "platform": platform,
                "name": name,
                "position": _to_str(row.get("favoritePosition")),
                "pro_pos": to_int(row.get("proPos")),
                "games_played": to_int(row.get("gamesPlayed")),
                "goals": to_int(row.get("goals")),
                "assists": to_int(row.get("assists")),
                "man_of_the_match": to_int(row.get("manOfTheMatch")),
                "rating_avg": to_float(row.get("ratingAve")),
                "prev_goals": to_int(row.get("prevGoals")),
                "raw_json": json.dumps(row, separators=(",", ":"), sort_keys=True),
                "updated_at": now,
            }
        )
    return out


def parse_team_stats(club_id: str, match_id: str, agg: dict[str, object]) -> dict[str, object]:
    """Extract normalised team aggregate stats from a raw EA aggregate dict."""
    raw_aggregate = json.dumps(agg, separators=(",", ":"), sort_keys=True)

    # possession may be expressed as a ratio 0-1 or percent 0-100
    raw_poss = to_float(agg.get("possession"))
    if raw_poss is not None and raw_poss <= 1.0:
        raw_poss = raw_poss * 100.0

    return {
        "match_id": match_id,
        "club_id": club_id,
        "source": "ea_aggregate",
        "goals": to_int(agg.get("goals")),
        "shots": to_int(agg.get("shots")),
        "shots_on_target": to_int(agg.get("shotsontarget")),
        "passes_made": to_int(agg.get("passesmade")),
        "pass_attempts": to_int(agg.get("passattempts")),
        "tackles_made": to_int(agg.get("tacklesmade")),
        "tackle_attempts": to_int(agg.get("tackleattempts")),
        "saves": to_int(agg.get("saves")),
        "goals_conceded": to_int(agg.get("goalsconceded")),
        "fouls": to_int(agg.get("fouls")),
        "corners": to_int(agg.get("corners")),
        "possession_pct": raw_poss,
        "raw_aggregate": raw_aggregate,
    }
