"""Result review layer for matches.

EA's friendly endpoint is not a reliable source of truth for two situations:

1. a match is started and immediately abandoned by both clubs (they leave to
   start another one) — EA still lists it, usually with a short duration and no
   real statistics;
2. in a group-stage match one club quits while the game is drawn — EA reports a
   default 3x0 (or 0x3) win that does not represent what happened.

This module detects those patterns, keeps the original EA score for auditing
and exposes helpers to ignore, confirm or correct a result. Corrected/ignored
matches are never overwritten by a later ingest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.result import calc_result
from app.db.models import (
    Championship,
    ClubMatch,
    Match,
    MatchClassification,
    MatchTeamStats,
    PlayerMatchStats,
)

# ── Review status ─────────────────────────────────────────────────────────

STATUS_VALID = "valid"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_IGNORED = "ignored"
STATUS_CORRECTED = "corrected"

REVIEW_STATUSES = (
    STATUS_VALID,
    STATUS_NEEDS_REVIEW,
    STATUS_IGNORED,
    STATUS_CORRECTED,
)
COUNTED_STATUSES = (STATUS_VALID, STATUS_CORRECTED)

STATUS_LABELS = {
    STATUS_VALID: "Válida",
    STATUS_NEEDS_REVIEW: "Aguardando revisão",
    STATUS_IGNORED: "Ignorada",
    STATUS_CORRECTED: "Corrigida",
}

STATUS_BADGES = {
    STATUS_VALID: "win",
    STATUS_NEEDS_REVIEW: "warn",
    STATUS_IGNORED: "ignored",
    STATUS_CORRECTED: "corrected",
}

# ── Reasons ───────────────────────────────────────────────────────────────

REASON_ABANDONED_BOTH = "abandoned_both"
REASON_FORFEIT_3X0 = "forfeit_3x0"
REASON_STATS_MISMATCH = "stats_mismatch"
REASON_SHORT_MATCH = "short_match"
REASON_DNF = "dnf"
REASON_MANUAL_CORRECTION = "manual_correction"
REASON_MANUAL_IGNORE = "manual_ignore"
REASON_MANUAL_CONFIRM = "manual_confirm"

AUTO_REASONS = (
    REASON_ABANDONED_BOTH,
    REASON_FORFEIT_3X0,
    REASON_STATS_MISMATCH,
    REASON_SHORT_MATCH,
    REASON_DNF,
)

REASON_LABELS = {
    REASON_ABANDONED_BOTH: "Partida abandonada pelos dois clubes (sem resultado real)",
    REASON_FORFEIT_3X0: "Placar 3x0/0x3 típico de desistência (W.O.)",
    REASON_STATS_MISMATCH: "Placar não confere com as estatísticas da partida",
    REASON_SHORT_MATCH: "Partida curta demais para ter sido concluída",
    REASON_DNF: "Desistência (DNF) reportada pela EA",
    REASON_MANUAL_CORRECTION: "Resultado corrigido manualmente",
    REASON_MANUAL_IGNORE: "Resultado ignorado manualmente",
    REASON_MANUAL_CONFIRM: "Resultado confirmado manualmente",
}

# ── Result source ─────────────────────────────────────────────────────────

SOURCE_EA = "ea"
SOURCE_MANUAL = "manual"
SOURCE_SCREENSHOT = "screenshot"

SOURCE_LABELS = {
    SOURCE_EA: "EA",
    SOURCE_MANUAL: "Manual",
    SOURCE_SCREENSHOT: "Print da partida",
}

# Abandoned right after kick-off: no real football was played.
ABANDONED_MAX_SECONDS = 120
# A sterile 0x0 that lasted less than this is treated as an abandoned match.
ABANDONED_DRAW_MAX_SECONDS = 15 * 60
# A finished match shorter than this with a non-zero score is suspicious.
SHORT_MATCH_MAX_SECONDS = 30 * 60


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_counted(status: str | None) -> bool:
    """Whether a match with this status should appear in aggregates."""
    return (status or STATUS_VALID) in COUNTED_STATUSES


@dataclass(frozen=True)
class MatchSignals:
    score_a: int
    score_b: int
    max_seconds: int | None
    aggregate_goals: int
    player_goals: int
    total_shots: int
    total_passes: int
    has_team_stats: bool
    has_player_stats: bool


@dataclass(frozen=True)
class Detection:
    status: str
    reason: str
    note: str


def collect_signals(db: Session, match: Match) -> MatchSignals:
    team_rows = (
        db.query(MatchTeamStats)
        .filter(MatchTeamStats.match_id == match.match_id)
        .all()
    )
    player_rows = (
        db.query(PlayerMatchStats)
        .filter(PlayerMatchStats.match_id == match.match_id)
        .all()
    )
    max_seconds = (
        max((row.seconds_played or 0) for row in player_rows)
        if player_rows
        else None
    )
    return MatchSignals(
        score_a=match.club_a_goals or 0,
        score_b=match.club_b_goals or 0,
        max_seconds=max_seconds,
        aggregate_goals=sum((row.goals or 0) for row in team_rows),
        player_goals=sum((row.goals or 0) for row in player_rows),
        total_shots=sum((row.shots or 0) for row in team_rows),
        total_passes=sum((row.passes_made or 0) for row in team_rows),
        has_team_stats=bool(team_rows),
        has_player_stats=bool(player_rows),
    )


def _is_group_stage(
    classification: MatchClassification | None,
    championship: Championship | None,
) -> bool:
    if classification is not None:
        if (classification.group_name or "").strip():
            return True
        stage = (classification.stage or "").lower()
        if "grupo" in stage or "group" in stage:
            return True
    if championship is not None and championship.format in ("groups", "mixed"):
        return True
    return False


def detect_match_issue(db: Session, match: Match) -> Detection | None:
    """Return a detection when the EA result looks unreliable.

    Manual decisions (``ignored``/``corrected``) are never overridden by this
    function; the caller decides whether to apply the detection.
    """
    if match.review_status in (STATUS_IGNORED, STATUS_CORRECTED):
        return None

    signals = collect_signals(db, match)
    score = (signals.score_a, signals.score_b)
    total_goals = sum(score)

    classification = db.get(MatchClassification, match.match_id)
    championship = (
        db.get(Championship, classification.championship_id)
        if classification is not None
        else None
    )

    # 1) Match started but abandoned by both clubs (or never really played).
    no_data = not signals.has_team_stats and not signals.has_player_stats
    tiny_match = (
        signals.max_seconds is not None
        and signals.max_seconds <= ABANDONED_MAX_SECONDS
    )
    sterile_draw = (
        score == (0, 0)
        and signals.total_shots == 0
        and signals.total_passes <= 30
        and (
            signals.max_seconds is None
            or signals.max_seconds <= ABANDONED_DRAW_MAX_SECONDS
        )
    )
    if no_data or tiny_match or sterile_draw:
        return Detection(
            STATUS_IGNORED,
            REASON_ABANDONED_BOTH,
            "A partida não teve resultado real (abandonada pelos dois clubes "
            "ou sem estatísticas). O placar da EA foi ignorado.",
        )

    # 2) Default forfeit scoreline: 3x0 / 0x3.
    if score in ((3, 0), (0, 3)):
        if _is_group_stage(classification, championship):
            return Detection(
                STATUS_NEEDS_REVIEW,
                REASON_FORFEIT_3X0,
                "Placar 3x0/0x3 em fase de grupos. Pode ser W.O. por "
                "desistência; confirme ou envie um print do resultado real.",
            )
        if match.dnf:
            return Detection(
                STATUS_NEEDS_REVIEW,
                REASON_DNF,
                "A EA reportou desistência (DNF) e aplicou o placar padrão.",
            )
        # Only the players' goals are trustworthy here: EA's team aggregate
        # regularly disagrees with the official scoreline (e.g. 0x3 with
        # aggregate goals of 5 against 27), so it must not drive this check.
        # A real 3x0 always has the 3 goals credited to players; a W.O. has
        # none. (Player totals per side may differ, e.g. the same goal also
        # credited in balances, so only the zero case is decisive.)
        if signals.player_goals == 0:
            return Detection(
                STATUS_NEEDS_REVIEW,
                REASON_STATS_MISMATCH,
                "O placar 3x0/0x3 não está registrado em nenhum gol de jogador. "
                "Provável W.O. por desistência.",
            )
        if (
            signals.max_seconds is not None
            and signals.max_seconds < SHORT_MATCH_MAX_SECONDS
        ):
            return Detection(
                STATUS_NEEDS_REVIEW,
                REASON_SHORT_MATCH,
                "Placar 3x0/0x3 em uma partida muito curta. Provável W.O.",
            )
        return None

    # 3) Other matches flagged by EA as DNF or clearly unfinished.
    if match.dnf:
        return Detection(
            STATUS_NEEDS_REVIEW,
            REASON_DNF,
            "A EA reportou desistência (DNF) nesta partida.",
        )
    if (
        total_goals > 0
        and signals.max_seconds is not None
        and signals.max_seconds < SHORT_MATCH_MAX_SECONDS
    ):
        return Detection(
            STATUS_NEEDS_REVIEW,
            REASON_SHORT_MATCH,
            "Partida encerrada antes do tempo normal; o placar pode não "
            "representar o resultado real.",
        )
    return None


def sync_club_matches(db: Session, match: Match) -> None:
    """Keep the per-club rows in sync with the effective match result."""
    ignored = 0 if is_counted(match.review_status) else 1
    perspectives = (
        (match.club_a_id, match.club_a_goals or 0, match.club_b_goals or 0),
        (match.club_b_id, match.club_b_goals or 0, match.club_a_goals or 0),
    )
    for club_id, goals_for, goals_against in perspectives:
        if not club_id:
            continue
        club_match = db.get(
            ClubMatch, {"club_id": club_id, "match_id": match.match_id}
        )
        is_win, is_draw, is_loss = calc_result(goals_for, goals_against)
        if club_match is None:
            club_match = ClubMatch(
                club_id=club_id,
                match_id=match.match_id,
                platform=match.platform or "common-gen5",
                goals_for=goals_for,
                goals_against=goals_against,
                is_win=is_win,
                is_draw=is_draw,
                is_loss=is_loss,
                is_ignored=ignored,
            )
            db.add(club_match)
            continue
        club_match.goals_for = goals_for
        club_match.goals_against = goals_against
        club_match.is_win = is_win
        club_match.is_draw = is_draw
        club_match.is_loss = is_loss
        club_match.is_ignored = ignored


def set_review_status(
    db: Session,
    match: Match,
    status: str,
    *,
    reason: str | None = None,
    note: str | None = None,
) -> Match:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Invalid review status: {status!r}")
    match.review_status = status
    match.review_reason = reason
    match.review_note = note
    match.reviewed_at = now_iso()
    sync_club_matches(db, match)
    return match


def auto_review_match(db: Session, match_id: str) -> Detection | None:
    """Re-evaluate one match and apply an automatic detection.

    A previously corrected/ignored match is left untouched. A match that was
    only automatically flagged is restored to ``valid`` when it no longer looks
    suspicious (for example after being removed from a group stage).
    """
    match = db.get(Match, match_id)
    if match is None:
        return None

    if match.review_status in (STATUS_IGNORED, STATUS_CORRECTED):
        return None

    detection = detect_match_issue(db, match)
    if detection is not None:
        set_review_status(
            db,
            match,
            detection.status,
            reason=detection.reason,
            note=detection.note,
        )
        return detection

    if (
        match.review_status == STATUS_NEEDS_REVIEW
        and match.review_reason in AUTO_REASONS
    ):
        set_review_status(db, match, STATUS_VALID, reason=None, note=None)
    return None


def scan_matches(db: Session) -> dict[str, int]:
    """Run the automatic review over every match. Returns a small summary."""
    matches = db.query(Match).all()
    changed = 0
    ignored = 0
    needs_review = 0
    for match in matches:
        before = match.review_status
        auto_review_match(db, match.match_id)
        if match.review_status != before:
            changed += 1
        if match.review_status == STATUS_IGNORED:
            ignored += 1
        elif match.review_status == STATUS_NEEDS_REVIEW:
            needs_review += 1
    db.flush()
    return {
        "scanned": len(matches),
        "changed": changed,
        "ignored": ignored,
        "needs_review": needs_review,
    }


def apply_manual_result(
    db: Session,
    match: Match,
    club_a_goals: int,
    club_b_goals: int,
    *,
    source: str = SOURCE_MANUAL,
    note: str | None = None,
) -> Match:
    """Replace the scoreline with the real result (manual or from a print)."""
    if club_a_goals < 0 or club_b_goals < 0:
        raise ValueError("Goals cannot be negative")

    if match.ea_club_a_goals is None:
        match.ea_club_a_goals = match.club_a_goals
    if match.ea_club_b_goals is None:
        match.ea_club_b_goals = match.club_b_goals

    match.club_a_goals = int(club_a_goals)
    match.club_b_goals = int(club_b_goals)
    match.result_source = source
    match.review_status = STATUS_CORRECTED
    match.review_reason = REASON_MANUAL_CORRECTION
    match.review_note = note or "Resultado corrigido pelo usuário."
    match.reviewed_at = now_iso()
    sync_club_matches(db, match)
    return match


def ignore_match(db: Session, match: Match, note: str | None = None) -> Match:
    return set_review_status(
        db,
        match,
        STATUS_IGNORED,
        reason=REASON_MANUAL_IGNORE,
        note=note or "Resultado ignorado pelo usuário.",
    )


def confirm_match(db: Session, match: Match, note: str | None = None) -> Match:
    return set_review_status(
        db,
        match,
        STATUS_VALID,
        reason=REASON_MANUAL_CONFIRM,
        note=note or "Resultado confirmado pelo usuário.",
    )


def review_view(match: Match) -> dict[str, object]:
    """Presentation dict shared by the API, dashboard and templates."""
    status = match.review_status or STATUS_VALID
    ea_a = match.ea_club_a_goals
    ea_b = match.ea_club_b_goals
    return {
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "badge": STATUS_BADGES.get(status, "draw"),
        "reason": match.review_reason,
        "reason_label": REASON_LABELS.get(match.review_reason or "", None),
        "note": match.review_note,
        "reviewed_at": match.reviewed_at,
        "result_source": match.result_source or SOURCE_EA,
        "result_source_label": SOURCE_LABELS.get(
            match.result_source or SOURCE_EA,
            match.result_source or SOURCE_EA,
        ),
        "is_counted": is_counted(status),
        "is_ignored": status == STATUS_IGNORED,
        "needs_review": status == STATUS_NEEDS_REVIEW,
        "ea_score": (
            f"{ea_a}–{ea_b}" if ea_a is not None and ea_b is not None else None
        ),
        "score_changed": (
            status == STATUS_CORRECTED
            and ea_a is not None
            and ea_b is not None
            and (ea_a != match.club_a_goals or ea_b != match.club_b_goals)
        ),
    }


def review_queue(
    db: Session,
    statuses: tuple[str, ...] = (STATUS_NEEDS_REVIEW, STATUS_IGNORED),
) -> list[Match]:
    return (
        db.query(Match)
        .filter(Match.review_status.in_(list(statuses)))
        .order_by(Match.played_at.desc())
        .all()
    )
