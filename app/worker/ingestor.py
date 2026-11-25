from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import structlog
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.decoder import decode_events
from app.core.member_link import link_members
from app.core.parser import (
    canonical_payload_json,
    parse_match,
    parse_member_career,
    parse_member_stats,
    parse_overall_stats,
    parse_player,
    parse_team_stats,
    sha256_hex,
)
from app.core.result import calc_result
from app.core.review import (
    STATUS_CORRECTED,
    STATUS_IGNORED,
    auto_review_match,
)
from app.db.models import (
    Club,
    ClubMatch,
    ClubMember,
    ClubMemberCareer,
    ClubOverallStats,
    IngestRun,
    Match,
    MatchPayloadArchive,
    MatchTeamStats,
    Player,
    PlayerMatchEvent,
    PlayerMatchMetrics,
    PlayerMatchStats,
    PlayerName,
)

log = structlog.get_logger(__name__)

# League and playoff payloads carry the per-player event telemetry; friendlies
# do not. All known types are ingested so the sample matches what EA has.
DEFAULT_MATCH_TYPES: tuple[str, ...] = ("leagueMatch", "friendlyMatch", "playoffMatch")
MATCHES_PER_TYPE = 10


class EAClient(Protocol):
    def get_club_matches(self, club_id: str, match_type: str, count: int) -> list[dict[str, object]]:
        ...


@dataclass
class IngestResult:
    run_id: int
    matches_returned: int = 0
    matches_inserted: int = 0
    matches_updated: int = 0
    profile_synced: bool = False
    members_synced: int = 0
    profile_error: str | None = None
    errors: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_run(session: Session, club_id: str) -> int:
    run = IngestRun(club_id=club_id, status="running", started_at=_now_iso())
    session.add(run)
    session.flush()
    session.commit()
    assert run.id is not None
    return int(run.id)


def _close_run(
    session: Session,
    run_id: int,
    *,
    status: str = "success",
    matches_returned: int = 0,
    matches_inserted: int = 0,
    matches_updated: int = 0,
    error: str | None = None,
    http_status: int | None = None,
) -> None:
    run = session.get(IngestRun, run_id)
    if run is None:
        return
    run.status = status
    run.finished_at = _now_iso()
    run.matches_returned = matches_returned
    run.matches_inserted = matches_inserted
    run.matches_updated = matches_updated
    run.error = error
    run.http_status = http_status
    session.commit()


def _upsert_club(
    session: Session,
    club_id: str,
    platform: str,
    name: str,
    raw_club: dict[str, object],
) -> None:
    """Upsert a club row, extracting shield/team data from the real EA shape.

    Real payload: ``TEAM`` at club level; name, teamId and crest inside
    ``details`` / ``details.customKit``.
    """
    now = _now_iso()
    details = raw_club.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    custom_kit = details.get("customKit") or {}
    if not isinstance(custom_kit, dict):
        custom_kit = {}

    team_id = raw_club.get("TEAM") or details.get("teamId")
    crest_asset_id = custom_kit.get("crestAssetId")
    region_id = details.get("regionId")

    # Crest URL: {base}/l{team}.png — TEAM is the asset id used by EA's CDN
    crest_url: str | None = None
    asset = team_id or crest_asset_id
    if asset and str(asset) != "0":
        crest_url = (
            "https://eafc24.content.easports.com/fifa/fltOnlineAssets/"
            "24B23FDE-7835-41C2-87A2-F453DFDB2E82/2024/fcweb/crests/256x256/"
            f"l{asset}.png"
        )

    existing = session.get(Club, {"club_id": club_id, "platform": platform})
    if existing is None:
        club = Club(
            club_id=club_id,
            platform=platform,
            name=name,
            team_id=str(team_id) if team_id not in (None, "") else None,
            crest_asset_id=str(crest_asset_id) if crest_asset_id not in (None, "") else None,
            crest_url=crest_url,
            stadium_name=None,
            region_id=str(region_id) if region_id not in (None, "") else None,
            first_seen_at=now,
            updated_at=now,
        )
        session.add(club)
    else:
        existing.name = name
        if team_id not in (None, ""):
            existing.team_id = str(team_id)
        if crest_asset_id not in (None, ""):
            existing.crest_asset_id = str(crest_asset_id)
        existing.crest_url = crest_url or existing.crest_url
        if region_id not in (None, ""):
            existing.region_id = str(region_id)
        existing.updated_at = now


def _upsert_match(
    session: Session, parsed: dict[str, object], platform: str
) -> tuple[bool, bool, Match]:
    """Return (inserted, updated, match).

    The effective score (``club_a_goals``/``club_b_goals``) is preserved when
    the user already corrected or ignored the result. The original EA score is
    always stored in ``ea_club_*_goals`` for auditing.
    """
    existing = session.get(Match, parsed["match_id"])
    if existing is None:
        m = Match(
            match_id=parsed["match_id"],
            platform=platform,
            match_type_code=parsed["match_type_code"],
            played_at=parsed["played_at"],
            season_id=parsed["season_id"],
            game_number=parsed["game_number"],
            dnf=parsed["dnf"],
            winner_by_dnf=parsed["winner_by_dnf"],
            club_a_id=parsed["club_a_id"],
            club_b_id=parsed["club_b_id"],
            club_a_goals=parsed["club_a_goals"],
            club_b_goals=parsed["club_b_goals"],
            ea_club_a_goals=parsed["club_a_goals"],
            ea_club_b_goals=parsed["club_b_goals"],
            review_status="valid",
            result_source="ea",
            raw_payload=parsed["raw_payload"],
            payload_hash=parsed["payload_hash"],
            parser_version=parsed["parser_version"],
            ingested_at=parsed["ingested_at"],
            first_seen_at=parsed["first_seen_at"],
            last_seen_at=parsed["last_seen_at"],
            seen_count=1,
        )
        session.add(m)
        return True, False, m

    existing.last_seen_at = _now_iso()
    existing.seen_count = (existing.seen_count or 0) + 1
    # Always refresh derived fields so parser fixes propagate on re-ingest.
    existing.match_type_code = parsed["match_type_code"]
    existing.played_at = parsed["played_at"]
    existing.season_id = parsed["season_id"]
    existing.game_number = parsed["game_number"]
    existing.dnf = parsed["dnf"]
    existing.winner_by_dnf = parsed["winner_by_dnf"]
    existing.club_a_id = parsed["club_a_id"]
    existing.club_b_id = parsed["club_b_id"]
    existing.ea_club_a_goals = parsed["club_a_goals"]
    existing.ea_club_b_goals = parsed["club_b_goals"]
    if existing.review_status not in (STATUS_CORRECTED, STATUS_IGNORED):
        existing.club_a_goals = parsed["club_a_goals"]
        existing.club_b_goals = parsed["club_b_goals"]
    payload_changed = existing.payload_hash != parsed["payload_hash"]
    if payload_changed:
        existing.raw_payload = str(parsed["raw_payload"])
        existing.payload_hash = str(parsed["payload_hash"])
        existing.parser_version = str(parsed["parser_version"])
    return False, True, existing


def _archive_payload(
    session: Session,
    match_id: str,
    raw: dict[str, object],
    parser_version: str,
) -> None:
    """Store the raw EA payload once per distinct version (append-only)."""
    payload_json = json.dumps(raw, separators=(",", ":"), sort_keys=True)
    payload_hash = sha256_hex(canonical_payload_json(raw))
    key = {"match_id": match_id, "payload_hash": payload_hash}
    if session.get(MatchPayloadArchive, key) is None:
        now = _now_iso()
        session.add(
            MatchPayloadArchive(
                match_id=match_id,
                payload_hash=payload_hash,
                parser_version=parser_version,
                payload_json=payload_json,
                captured_at=now,
                first_seen_at=now,
            )
        )


def _upsert_club_match(
    session: Session,
    club_id: str,
    match_id: str,
    platform: str,
    goals_for: int,
    goals_against: int,
    is_ignored: int = 0,
) -> None:
    is_win, is_draw, is_loss = calc_result(goals_for, goals_against)
    existing = session.get(ClubMatch, {"club_id": club_id, "match_id": match_id})
    if existing is None:
        cm = ClubMatch(
            club_id=club_id,
            match_id=match_id,
            platform=platform,
            goals_for=goals_for,
            goals_against=goals_against,
            is_win=is_win,
            is_draw=is_draw,
            is_loss=is_loss,
            is_ignored=is_ignored,
        )
        session.add(cm)
    else:
        existing.goals_for = goals_for
        existing.goals_against = goals_against
        existing.is_win = is_win
        existing.is_draw = is_draw
        existing.is_loss = is_loss
        existing.is_ignored = is_ignored


def _upsert_team_stats(session: Session, stats: dict[str, object]) -> None:
    key = {"match_id": stats["match_id"], "club_id": stats["club_id"]}
    existing = session.get(MatchTeamStats, key)
    if existing is None:
        ts = MatchTeamStats(**stats)
        session.add(ts)
    else:
        for k, v in stats.items():
            setattr(existing, k, v)


def _upsert_player(session: Session, player_key: str, ea_player_id: str | None, platform: str) -> None:
    now = _now_iso()
    existing = session.get(Player, player_key)
    if existing is None:
        p = Player(
            player_key=player_key,
            ea_player_id=ea_player_id,
            platform=platform,
            first_seen_at=now,
            updated_at=now,
        )
        session.add(p)
    else:
        existing.updated_at = now
        if ea_player_id and not existing.ea_player_id:
            existing.ea_player_id = ea_player_id


def _upsert_player_name(session: Session, player_key: str, playername: str) -> None:
    now = _now_iso()
    existing = session.get(PlayerName, {"player_key": player_key, "playername": playername})
    if existing is None:
        pn = PlayerName(
            player_key=player_key,
            playername=playername,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(pn)
    else:
        existing.last_seen_at = now


def _upsert_player_match_stats(session: Session, stats: dict[str, object]) -> None:
    key = {"match_id": stats["match_id"], "player_key": stats["player_key"]}
    existing = session.get(PlayerMatchStats, key)
    if existing is None:
        pms = PlayerMatchStats(**stats)
        session.add(pms)
    else:
        for k, v in stats.items():
            setattr(existing, k, v)


def _upsert_player_match_events(
    session: Session,
    match_id: str,
    player_key: str,
    raw_events: dict[int, int],
) -> None:
    for eid, count in raw_events.items():
        key = {"match_id": match_id, "player_key": player_key, "event_id": eid, "bucket": 0}
        existing = session.get(PlayerMatchEvent, key)
        if existing is None:
            ev = PlayerMatchEvent(
                match_id=match_id,
                player_key=player_key,
                event_id=eid,
                count=count,
                bucket=0,
            )
            session.add(ev)
        else:
            existing.count = count


def _upsert_player_match_metrics(
    session: Session,
    match_id: str,
    player_key: str,
    named_metrics: dict[str, int],
) -> None:
    key = {"match_id": match_id, "player_key": player_key}
    existing = session.get(PlayerMatchMetrics, key)
    if existing is None:
        pmm = PlayerMatchMetrics(
            match_id=match_id,
            player_key=player_key,
            **named_metrics,
        )
        session.add(pmm)
    else:
        for k, v in named_metrics.items():
            if hasattr(existing, k):
                setattr(existing, k, v)


def _process_players(
    session: Session,
    match_id: str,
    club_id: str,
    platform: str,
    players_data: dict[str, object],
) -> None:
    for ea_id, p_raw in players_data.items():
        assert isinstance(p_raw, dict)
        player_key = ea_id
        ea_player_id = str(p_raw.get("eaPlayerId", "")) or None

        _upsert_player(session, player_key, ea_player_id, platform)
        playername = str(p_raw.get("playername", ""))
        if playername:
            _upsert_player_name(session, player_key, playername)

        player_stats = parse_player(ea_id, club_id, match_id, p_raw)
        _upsert_player_match_stats(session, player_stats)

        raw_events, named_metrics = decode_events(p_raw)
        _upsert_player_match_events(session, match_id, player_key, raw_events)
        _upsert_player_match_metrics(session, match_id, player_key, named_metrics)


def _profile_record(payload: Any) -> dict[str, Any] | None:
    """EA returns a one-row list for overall stats and an id-keyed dict for info."""
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], dict) else None
    if isinstance(payload, dict):
        if "members" in payload:
            return None
        values = [value for value in payload.values() if isinstance(value, dict)]
        return values[0] if values else payload
    return None


def _apply_columns(row: object, data: dict[str, Any], skip: tuple[str, ...]) -> None:
    for key, value in data.items():
        if key in skip:
            continue
        setattr(row, key, value)


def sync_club_profile(
    club_id: str,
    session: Session,
    client: object,
    platform: str = "common-gen5",
) -> dict[str, Any]:
    """Ingest EA's season snapshot: overall stats, current members, careers.

    Each endpoint is optional: a client that does not implement one is simply
    skipped, so test doubles and older clients keep working.
    """
    summary: dict[str, Any] = {"overall": False, "members": 0, "career": 0}

    overall_fn = getattr(client, "get_club_overall_stats", None)
    if callable(overall_fn):
        record = _profile_record(overall_fn(club_id))
        if record:
            parsed = parse_overall_stats(club_id, record, platform)
            key = {"club_id": parsed["club_id"], "platform": platform}
            row = session.get(ClubOverallStats, key)
            if row is None:
                session.add(ClubOverallStats(**parsed))
            else:
                _apply_columns(row, parsed, skip=("club_id", "platform"))
            summary["overall"] = True

    members_fn = getattr(client, "get_member_stats", None)
    if callable(members_fn):
        parsed_members = parse_member_stats(club_id, members_fn(club_id), platform)
        for parsed in parsed_members:
            key = {
                "club_id": parsed["club_id"],
                "platform": parsed["platform"],
                "name": parsed["name"],
            }
            row = session.get(ClubMember, key)
            if row is None:
                session.add(ClubMember(**parsed))
            else:
                _apply_columns(row, parsed, skip=("club_id", "platform", "name"))
        summary["members"] = len(parsed_members)

    career_fn = getattr(client, "get_member_career_stats", None)
    if callable(career_fn):
        parsed_career = parse_member_career(club_id, career_fn(club_id), platform)
        for parsed in parsed_career:
            key = {
                "club_id": parsed["club_id"],
                "platform": parsed["platform"],
                "name": parsed["name"],
            }
            row = session.get(ClubMemberCareer, key)
            if row is None:
                session.add(ClubMemberCareer(**parsed))
            else:
                _apply_columns(row, parsed, skip=("club_id", "platform", "name"))
        summary["career"] = len(parsed_career)

    session.flush()
    summary["links"] = link_members(session, club_id, platform)
    return summary


def _fetch_unique_matches(
    club_id: str,
    client: EAClient,
    match_types: tuple[str, ...],
    result: IngestResult,
) -> tuple[list[dict[str, object]], bool]:
    """Fetch every match type, de-duplicating ids seen in more than one call."""
    fetched: list[dict[str, object]] = []
    seen: set[str] = set()
    any_success = False

    for match_type in match_types:
        try:
            batch = client.get_club_matches(club_id, match_type, MATCHES_PER_TYPE)
            any_success = True
        except Exception as exc:
            log.error(
                "ingest.http_error", club_id=club_id, match_type=match_type, error=str(exc)
            )
            result.errors.append(f"{match_type}: {exc}")
            continue

        for raw in batch or []:
            match_id = str(raw.get("matchId") or "")
            if not match_id or match_id in seen:
                continue
            seen.add(match_id)
            fetched.append(raw)

        log.info(
            "ingest.fetched",
            club_id=club_id,
            match_type=match_type,
            count=len(batch or []),
        )

    return fetched, any_success


def ingest(
    club_id: str,
    session: Session,
    client: EAClient,
    parser_version: str,
    platform: str = "common-gen5",
    match_types: tuple[str, ...] = DEFAULT_MATCH_TYPES,
) -> IngestResult:
    """Run a full ingest cycle for the given club.

    Opens an ingest_run record, fetches every match type from EA, upserts the
    derived rows, refreshes the club/member season snapshot and closes the run.
    """
    run_id = _open_run(session, club_id)
    result = IngestResult(run_id=run_id)

    raw_matches, any_success = _fetch_unique_matches(
        club_id, client, match_types, result
    )
    if not any_success:
        _close_run(
            session,
            run_id,
            status="http_error",
            error="; ".join(result.errors) or "no match type fetched",
        )
        return result

    result.matches_returned = len(raw_matches)

    for raw in raw_matches:
        try:
            parsed = parse_match(raw, parser_version)
            match_id = str(parsed["match_id"])

            inserted, updated, match_obj = _upsert_match(session, parsed, platform)
            if inserted:
                result.matches_inserted += 1
            elif updated:
                result.matches_updated += 1

            # Preserve the original EA payload for the historical archive
            _archive_payload(session, match_id, raw, parser_version)

            clubs_data: dict[str, object] = raw.get("clubs", {})  # type: ignore[assignment]
            assert isinstance(clubs_data, dict)
            aggregate_data: dict[str, object] = raw.get("aggregate", {}) or {}  # type: ignore[assignment]
            assert isinstance(aggregate_data, dict)

            for cid, cdata in clubs_data.items():
                assert isinstance(cdata, dict)
                details = cdata.get("details") or {}
                assert isinstance(details, dict)
                cname = str(details.get("name") or cid)
                _upsert_club(session, cid, platform, cname, cdata)

                # club-level perspective — always use the effective score, so a
                # manual correction is not overwritten by the next ingest.
                gf = int(
                    match_obj.club_a_goals
                    if cid == match_obj.club_a_id
                    else match_obj.club_b_goals
                )
                ga = int(
                    match_obj.club_b_goals
                    if cid == match_obj.club_a_id
                    else match_obj.club_a_goals
                )
                _upsert_club_match(
                    session,
                    cid,
                    match_id,
                    platform,
                    gf,
                    ga,
                    is_ignored=int(match_obj.review_status == STATUS_IGNORED),
                )

                # team aggregate stats — usually top-level keyed by club id, but
                # some payloads nest it inside each club entry.
                agg = aggregate_data.get(cid)
                if not isinstance(agg, dict) or not agg:
                    nested_agg = cdata.get("aggregate")
                    if isinstance(nested_agg, dict):
                        agg = nested_agg
                agg = agg if isinstance(agg, dict) else {}
                team_stats = parse_team_stats(cid, match_id, agg)
                # EA's aggregate goals are unreliable (they regularly disagree
                # with the official scoreline), so the team row keeps the real
                # perspective score and the aggregate only as raw evidence.
                team_stats["goals"] = gf
                _upsert_team_stats(session, team_stats)

                # player stats
                players_data: dict[str, object] = raw.get("players", {}) or {}  # type: ignore[assignment]
                assert isinstance(players_data, dict)
                club_players = players_data.get(cid, {}) or {}
                assert isinstance(club_players, dict)
                _process_players(session, match_id, cid, platform, club_players)

            # Now that stats/players exist, derive the real match duration and
            # detect abandoned matches / suspicious 3x0 scorelines. This never
            # overrides a manual decision.
            session.flush()
            longest = (
                session.query(func.max(PlayerMatchStats.seconds_played))
                .filter(PlayerMatchStats.match_id == match_id)
                .scalar()
            )
            match_obj.duration_seconds = int(longest or 0) or None
            auto_review_match(session, match_id)

            session.commit()
            log.debug("ingest.match_done", match_id=match_id, inserted=inserted)

        except Exception as exc:
            session.rollback()
            log.error("ingest.match_error", error=str(exc), match=raw.get("matchId"))
            result.errors.append(str(exc))

    # Season snapshot (club overall + members + careers). Failures here are
    # reported separately: match ingestion already succeeded.
    try:
        profile = sync_club_profile(club_id, session, client, platform)
        session.commit()
        result.profile_synced = True
        result.members_synced = int(profile.get("members", 0))
        log.info(
            "ingest.profile_synced",
            club_id=club_id,
            overall=profile.get("overall"),
            members=profile.get("members"),
            career=profile.get("career"),
            links=profile.get("links"),
        )
    except Exception as exc:  # noqa: BLE001 - profile must never break matches
        session.rollback()
        result.profile_error = str(exc)
        log.error("ingest.profile_error", club_id=club_id, error=str(exc))

    try:
        _close_run(
            session,
            run_id,
            status="success",
            matches_returned=result.matches_returned,
            matches_inserted=result.matches_inserted,
            matches_updated=result.matches_updated,
            error=result.profile_error,
        )
    except Exception as exc:
        log.error("ingest.close_run_error", error=str(exc))

    log.info(
        "ingest.done",
        club_id=club_id,
        returned=result.matches_returned,
        inserted=result.matches_inserted,
        updated=result.matches_updated,
        errors=len(result.errors),
    )
    return result
