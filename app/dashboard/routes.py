from __future__ import annotations

import json
import pathlib
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_optional_user, get_settings
from app.config import Settings
from app.core.ea_public_cache import fetch_ea_public_club, search_ea_clubs_by_name
from app.core.payment import create_pix_checkout, PRO_PRICE_CENTS
from app.core.security import (
    create_user_session,
    delete_session,
    hash_password,
    now_iso,
    verify_password,
)
from app.db.models import PaymentOrder, TrackedClub, User
from app.core.championships import (
    build_campaign,
    championship_rows,
    classification_map,
    match_row,
    opponent_summary,
    parse_extra,
    serialize_extra,
    serialize_tags,
    stage_breakdown,
    stage_rows,
    top_scorers,
)
from app.core import analytics
from app.core.analytics import (
    MARGIN_FILTER_LABELS,
    MATCH_TYPE_FILTER_LABELS,
    POSITION_LABELS,
    RESULT_FILTER_LABELS,
    MatchFilters,
    dashboard_context,
    filtered_matches,
    h2h_summary,
    match_analytics,
    match_type_label,
    official_summary,
    pass_direction_players,
    player_aggregates,
    session_reports,
    sort_players,
    squad_view,
    team_profile,
)
from app.core.evidence import EvidenceError, extract_summary, save_image
from app.core.member_link import link_member, normalize_name
from app.core.review import (
    COUNTED_STATUSES,
    REASON_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS,
    apply_manual_result,
    auto_review_match,
    confirm_match,
    ignore_match,
    now_iso,
    review_queue,
    review_view,
    scan_matches,
)
from app.db.models import (
    Championship,
    Club,
    ClubMatch,
    ClubMember,
    ClubMemberCareer,
    ClubOverallStats,
    IngestRun,
    Match,
    MatchClassification,
    MatchEvidence,
    Player,
    PlayerMatchMetrics,
    PlayerMatchStats,
)

_templates_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

router = APIRouter(tags=["dashboard"])

FORMAT_LABELS = {
    "league": "Liga",
    "knockout": "Mata-mata",
    "groups": "Grupos",
    "mixed": "Misto",
    "other": "Outro",
}
STATUS_LABELS = {
    "active": "Ativo",
    "finished": "Encerrado",
    "archived": "Arquivado",
}
LEG_LABELS = {"ida": "Ida", "volta": "Volta", "unico": "Jogo único"}


def _club_name_map(db: Session, club_ids: set[str]) -> dict[str, str]:
    """Return a {club_id: display_name} map, falling back to the id."""
    if not club_ids:
        return {}
    rows = db.query(Club.club_id, Club.name).filter(Club.club_id.in_(club_ids)).all()
    return {cid: (name or cid) for cid, name in rows}


def _names_for_rows(db: Session, rows: list[tuple[ClubMatch, Match]]) -> dict[str, str]:
    ids: set[str] = set()
    for _club_match, match in rows:
        ids.update({match.club_a_id, match.club_b_id})
    return _club_name_map(db, ids)


def _opponent_options(db: Session, club_id: str) -> list[dict[str, object]]:
    """Every opponent the club has a match against, with a played counter."""
    rows = (
        db.query(Match.club_a_id, Match.club_b_id)
        .join(ClubMatch, ClubMatch.match_id == Match.match_id)
        .filter(ClubMatch.club_id == club_id, ClubMatch.is_ignored == 0)
        .all()
    )
    counts: dict[str, int] = {}
    for club_a, club_b in rows:
        opponent = club_b if club_a == club_id else club_a
        counts[opponent] = counts.get(opponent, 0) + 1
    names = _club_name_map(db, set(counts))
    return sorted(
        (
            {"club_id": cid, "name": names.get(cid, cid), "played": played}
            for cid, played in counts.items()
        ),
        key=lambda item: str(item["name"]).casefold(),
    )


def _filters_context(filters: MatchFilters) -> dict[str, object]:
    return {
        "filters": filters,
        "filter_query": filters.as_query(),
        "filter_description": filters.describe(),
        "type_labels": MATCH_TYPE_FILTER_LABELS,
        "result_labels": RESULT_FILTER_LABELS,
        "margin_labels": MARGIN_FILTER_LABELS,
        "position_labels": POSITION_LABELS,
    }


def _overall_for(db: Session, club_id: str, platform: str) -> ClubOverallStats | None:
    return db.get(
        ClubOverallStats, {"club_id": club_id, "platform": platform}
    )


def _metrics_by_match(
    db: Session, match_ids: list[str], player_key: str
) -> dict[str, PlayerMatchMetrics]:
    rows = (
        db.query(PlayerMatchMetrics)
        .filter(
            PlayerMatchMetrics.player_key == player_key,
            PlayerMatchMetrics.match_id.in_(match_ids),
        )
        .all()
    )
    return {row.match_id: row for row in rows}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _championship_select(db: Session) -> list[dict[str, object]]:
    rows = db.query(Championship).order_by(Championship.name.asc()).all()
    return [
        {"id": row.id, "name": row.name, "color": row.color, "status": row.status}
        for row in rows
    ]


def _championship_view(db: Session, championship: Championship) -> dict[str, object]:
    return {
        "id": championship.id,
        "name": championship.name,
        "format": championship.format,
        "format_label": FORMAT_LABELS.get(championship.format, championship.format),
        "status": championship.status,
        "status_label": STATUS_LABELS.get(championship.status, championship.status),
        "start_date": championship.start_date,
        "end_date": championship.end_date,
        "color": championship.color or "#3b82f6",
        "description": championship.description,
        "external_url": championship.external_url,
        "extra": parse_extra(championship.extra),
        "created_at": championship.created_at,
        "updated_at": championship.updated_at,
    }


def _match_query(
    db: Session,
    club_id: str,
    championship_filter: str | None,
    review_filter: str | None = None,
    match_type_filter: str | None = None,
):
    q = (
        db.query(ClubMatch, Match)
        .join(Match, Match.match_id == ClubMatch.match_id)
        .filter(ClubMatch.club_id == club_id)
    )
    if match_type_filter in analytics.MATCH_TYPE_FILTERS:
        q = q.filter(
            Match.match_type_code.in_(
                analytics.MATCH_TYPE_FILTERS[match_type_filter]
            )
        )
    if championship_filter == "none":
        q = q.outerjoin(
            MatchClassification, MatchClassification.match_id == Match.match_id
        ).filter(MatchClassification.match_id.is_(None))
    elif championship_filter and championship_filter.isdigit():
        q = q.join(
            MatchClassification, MatchClassification.match_id == Match.match_id
        ).filter(MatchClassification.championship_id == int(championship_filter))
    if review_filter == "pending":
        q = q.filter(Match.review_status.in_(("needs_review", "ignored")))
    elif review_filter in ("valid", "needs_review", "ignored", "corrected"):
        q = q.filter(Match.review_status == review_filter)
    return q


def _apply_form_classification(
    db: Session,
    match_id: str,
    championship_id: int,
    stage: str,
    round_name: str,
    leg: str,
    group_name: str,
    tags: str,
    notes: str,
) -> None:
    classification = db.get(MatchClassification, match_id)
    if classification is None:
        classification = MatchClassification(match_id=match_id, updated_at=now_iso())
        db.add(classification)
    classification.championship_id = championship_id
    classification.stage = _clean(stage)
    classification.round = _clean(round_name)
    classification.leg = _clean(leg)
    classification.group_name = _clean(group_name)
    classification.notes = _clean(notes)
    classification.tags = serialize_tags(tags)
    classification.updated_at = now_iso()
    # A group-stage 3x0 becomes suspicious only after classification.
    db.flush()
    auto_review_match(db, match_id)


@router.get("/landing", response_class=HTMLResponse)
def landing_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    """Hub inicial oficial do ecossistema Upzin (Autoplay & Scouts)."""
    return templates.TemplateResponse(
        request, "home.html", {"request": request, "current_user": user}
    )


@router.get("/autoplay", response_class=HTMLResponse)
def autoplay_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    """Página dedicada do Upzin Autoplay (evolução AFK no console)."""
    return templates.TemplateResponse(
        request, "autoplay.html", {"request": request, "current_user": user}
    )


@router.get("/scouts", response_class=HTMLResponse)
def scouts_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    """Página dedicada do Upzin Scouts (dados e telemetria oficial EA)."""
    return templates.TemplateResponse(
        request, "scouts.html", {"request": request, "current_user": user}
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_view(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    """Dashboard tático do clube com métricas, partidas e elenco."""
    tracked_clubs: list[TrackedClub] = []
    if user:
        tracked_clubs = (
            db.query(TrackedClub)
            .filter(TrackedClub.user_id == user.id, TrackedClub.is_active == 1)
            .all()
        )
        # Se o usuário cadastrado ainda não escolheu nenhum clube, redireciona para o onboarding de escolha
        if not tracked_clubs:
            return RedirectResponse(
                url="/meus-clubes?onboarding=1&msg=Bem-vindo+ao+Upzin+Scouts!+Busque+seu+clube+pelo+nome+para+iniciar.",
                status_code=303,
            )

    # Determinar qual clube exibir
    requested_id = request.query_params.get("club_id")
    if requested_id:
        club_id = requested_id.strip()
        matched = next((tc for tc in tracked_clubs if tc.club_id == club_id), None)
        club_name = matched.club_name if (matched and matched.club_name) else f"Clube {club_id}"
    elif tracked_clubs:
        club_id = tracked_clubs[0].club_id
        club_name = tracked_clubs[0].club_name or f"Clube {club_id}"
    else:
        # Visitante anônimo (modo demonstração de exemplo com MVL ES)
        club_id = settings.CLUB_ID
        club_name = settings.CLUB_NAME

    filters = MatchFilters.from_query(request.query_params)
    rows = filtered_matches(db, club_id, filters)
    total_stored_matches = len(rows)

    is_pro = bool(user and user.plan_tier == "pro")
    free_limit_applied = False
    hidden_matches_count = 0

    if not is_pro and total_stored_matches > 20:
        free_limit_applied = True
        hidden_matches_count = total_stored_matches - 20
        rows = rows[:20]

    names = _names_for_rows(db, rows)
    context = dashboard_context(
        db, club_id, settings.PLATFORM, rows, names, filters
    )

    pending_reviews = (
        db.query(func.count(Match.match_id))
        .filter(Match.review_status == "needs_review")
        .scalar()
        or 0
    )
    latest_run = (
        db.query(IngestRun).order_by(IngestRun.started_at.desc()).first()
    )

    context.update(
        {
            "club_id": club_id,
            "club_name": club_name,
            "tracked_clubs": tracked_clubs,
            "is_demo_mode": (user is None or not tracked_clubs),
            "pending_reviews": pending_reviews,
            "opponents": _opponent_options(db, club_id),
            "latest_run": latest_run,
            "current_user": user,
            "is_pro": is_pro,
            "user_plan": user.plan_tier if user else "free",
            "total_stored_matches": total_stored_matches,
            "free_limit_applied": free_limit_applied,
            "hidden_matches_count": hidden_matches_count,
            **_filters_context(filters),
        }
    )
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    # Se o visitante não estiver logado, exibe o Hub inicial com os dois caminhos
    if not user:
        return templates.TemplateResponse(
            request, "home.html", {"request": request, "current_user": None}
        )
    # Se estiver logado, exibe o Dashboard do Clube
    return dashboard_view(request=request, db=db, settings=settings, user=user)


@router.get("/partidas", response_class=HTMLResponse)
def matches_list(
    request: Request,
    page: int = 1,
    campeonato: str | None = None,
    revisao: str | None = None,
    tipo: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    club_id = settings.CLUB_ID
    per_page = 100
    page = max(page, 1)
    championship_filter = campeonato or None
    review_filter = revisao or None
    match_type_filter = tipo if tipo in analytics.MATCH_TYPE_FILTERS else None

    is_pro = bool(user and user.plan_tier == "pro")

    total = (
        _match_query(
            db, club_id, championship_filter, review_filter, match_type_filter
        )
        .with_entities(func.count(ClubMatch.match_id))
        .scalar()
        or 0
    )
    rows = (
        _match_query(
            db, club_id, championship_filter, review_filter, match_type_filter
        )
        .order_by(Match.played_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    names = _club_name_map(
        db,
        {m.club_a_id for _, m in rows} | {m.club_b_id for _, m in rows},
    )
    classifications = classification_map(db, [m.match_id for _, m in rows])
    matches_out = [
        match_row(cm, m, names, classifications.get(m.match_id)) for cm, m in rows
    ]

    free_limit_applied = False
    hidden_matches_count = 0
    if not is_pro and total > 20:
        free_limit_applied = True
        hidden_matches_count = total - 20
        matches_out = matches_out[:20]
        total_pages = 1
    else:
        total_pages = max((total + per_page - 1) // per_page, 1)

    championships = _championship_select(db)

    def _page_url(target: int) -> str:
        suffix = f"&campeonato={quote(str(championship_filter))}" if championship_filter else ""
        if review_filter:
            suffix += f"&revisao={quote(str(review_filter))}"
        if match_type_filter:
            suffix += f"&tipo={quote(match_type_filter)}"
        return f"/partidas?page={target}{suffix}"

    return templates.TemplateResponse(
        request,
        "matches.html",
        {
            "matches": matches_out,
            "club_id": club_id,
            "club_name": settings.CLUB_NAME,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "prev_url": _page_url(page - 1),
            "next_url": _page_url(page + 1),
            "championships": championships,
            "championship_filter": championship_filter or "",
            "review_filter": review_filter or "",
            "match_type_filter": match_type_filter or "",
            "type_labels": MATCH_TYPE_FILTER_LABELS,
            "review_status_labels": STATUS_LABELS,
            "format_labels": FORMAT_LABELS,
            "current_user": user,
            "is_pro": is_pro,
            "user_plan": user.plan_tier if user else "free",
            "free_limit_applied": free_limit_applied,
            "hidden_matches_count": hidden_matches_count,
        },
    )


def _evidence_views(db: Session, match_id: str) -> list[dict[str, object]]:
    rows = (
        db.query(MatchEvidence)
        .filter(MatchEvidence.match_id == match_id)
        .order_by(MatchEvidence.created_at.desc())
        .all()
    )
    views: list[dict[str, object]] = []
    for row in rows:
        extracted: object | None = None
        if row.extracted_json:
            try:
                extracted = json.loads(row.extracted_json)
            except ValueError:
                extracted = None
        views.append(
            {
                "id": row.id,
                "original_name": row.original_name,
                "content_type": row.content_type,
                "size_bytes": row.size_bytes,
                "extractor": row.extractor,
                "created_at": row.created_at,
                "extracted": extracted,
            }
        )
    return views


def _match_detail_context(
    db: Session,
    settings: Settings,
    match_id: str,
    *,
    extraction: object | None = None,
    message: str | None = None,
    error: str | None = None,
) -> dict[str, object]:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")

    players = db.query(PlayerMatchStats).filter(PlayerMatchStats.match_id == match_id).all()
    # Sort by rating (descending) with unavailable ratings (None) last, so the
    # template can render both teams in a stable order.
    players = sorted(
        players,
        key=lambda p: p.rating if p.rating is not None else -1.0,
        reverse=True,
    )

    club_rows = (
        db.query(Club)
        .filter(Club.club_id.in_([match.club_a_id, match.club_b_id]))
        .all()
    )
    clubs = {c.club_id: c for c in club_rows}

    def _club_view(cid: str) -> dict[str, object]:
        c = clubs.get(cid)
        return {
            "id": cid,
            "name": c.name if c and c.name else cid,
            "crest_url": c.crest_url if c else None,
        }

    classification = classification_map(db, [match_id]).get(match_id)
    stage_suggestions = [
        stage
        for (stage,) in db.query(MatchClassification.stage)
        .filter(MatchClassification.stage.isnot(None))
        .distinct()
        .all()
        if stage
    ]

    analytics_view = match_analytics(
        db, match_id, [match.club_a_id, match.club_b_id]
    )
    review = review_view(match)
    suggested_a = match.club_a_goals
    suggested_b = match.club_b_goals
    if extraction is not None:
        data = getattr(extraction, "data", {}) or {}
        if data.get("club_a_goals") is not None:
            suggested_a = data.get("club_a_goals")
        if data.get("club_b_goals") is not None:
            suggested_b = data.get("club_b_goals")

    return {
        "match": match,
        "players": players,
        "club_a": _club_view(match.club_a_id),
        "club_b": _club_view(match.club_b_id),
        "analytics": analytics_view,
        "match_type_label": match_type_label(match.match_type_code),
        "club_name": settings.CLUB_NAME,
        "club_id": settings.CLUB_ID,
        "classification": classification,
        "championships": _championship_select(db),
        "stage_suggestions": sorted(set(stage_suggestions)),
        "leg_labels": LEG_LABELS,
        "review": review,
        "review_status_labels": STATUS_LABELS,
        "review_reason_labels": REASON_LABELS,
        "source_labels": SOURCE_LABELS,
        "evidence": _evidence_views(db, match_id),
        "extraction": extraction,
        "suggested_a": suggested_a,
        "suggested_b": suggested_b,
        "message": message,
        "error": error,
    }


@router.get("/partidas/{match_id}", response_class=HTMLResponse)
def match_detail(
    match_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    context = _match_detail_context(db, settings, match_id)
    return templates.TemplateResponse(request, "match_detail.html", context)


@router.post("/partidas/{match_id}/revisao/confirmar")
def match_review_confirm(
    match_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")
    confirm_match(db, match, _clean(note))
    db.commit()
    return RedirectResponse(url=f"/partidas/{match_id}?msg=Resultado+confirmado", status_code=303)


@router.post("/partidas/{match_id}/revisao/ignorar")
def match_review_ignore(
    match_id: str,
    note: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")
    ignore_match(db, match, _clean(note))
    db.commit()
    return RedirectResponse(url=f"/partidas/{match_id}?msg=Resultado+ignorado", status_code=303)


@router.post("/partidas/{match_id}/revisao/restaurar")
def match_review_restore(
    match_id: str,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")
    confirm_match(db, match, "Resultado restaurado pelo usuário.")
    auto_review_match(db, match_id)
    db.commit()
    return RedirectResponse(url=f"/partidas/{match_id}?msg=Partida+restaurada", status_code=303)


@router.post("/partidas/{match_id}/revisao/corrigir")
def match_review_correct(
    match_id: str,
    club_a_goals: int = Form(...),
    club_b_goals: int = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")
    try:
        apply_manual_result(
            db,
            match,
            club_a_goals,
            club_b_goals,
            note=_clean(note),
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/partidas/{match_id}?erro={quote(str(exc))}", status_code=303
        )
    db.commit()
    return RedirectResponse(url=f"/partidas/{match_id}?msg=Placar+corrigido", status_code=303)


@router.post("/partidas/{match_id}/evidencia")
async def match_evidence_upload(
    match_id: str,
    request: Request,
    file: UploadFile = File(...),
    apply: bool = Form(False),
    note: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")

    data = await file.read()
    try:
        stored = save_image(
            data,
            file.filename,
            file.content_type,
            match_id,
            settings,
        )
    except EvidenceError as exc:
        context = _match_detail_context(db, settings, match_id, error=str(exc))
        return templates.TemplateResponse(
            request, "match_detail.html", context, status_code=400
        )

    names = _club_name_map(db, {match.club_a_id, match.club_b_id})
    extraction = extract_summary(
        pathlib.Path(stored.stored_path),
        settings,
        club_a_name=names.get(match.club_a_id),
        club_b_name=names.get(match.club_b_id),
        content_type=stored.content_type,
    )

    evidence = MatchEvidence(
        match_id=match_id,
        stored_path=stored.stored_path,
        original_name=stored.original_name,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        extractor=extraction.extractor,
        extracted_json=json.dumps(
            {
                "data": extraction.data,
                "raw_text": extraction.raw_text,
                "warnings": extraction.warnings,
            },
            ensure_ascii=False,
        ),
        created_at=now_iso(),
    )
    db.add(evidence)
    db.flush()

    applied = False
    if apply and extraction.has_score:
        apply_manual_result(
            db,
            match,
            int(extraction.data["club_a_goals"]),  # type: ignore[arg-type]
            int(extraction.data["club_b_goals"]),  # type: ignore[arg-type]
            source="screenshot",
            note=_clean(note) or "Resultado extraído do print da partida.",
        )
        applied = True
    db.commit()

    message = (
        "Print salvo e placar aplicado automaticamente."
        if applied
        else "Print salvo. Confira a sugestão abaixo e salve a correção."
    )
    context = _match_detail_context(
        db,
        settings,
        match_id,
        extraction=extraction,
        message=message,
    )
    return templates.TemplateResponse(request, "match_detail.html", context)


@router.get("/evidencias/{evidence_id}")
def evidence_file(
    evidence_id: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    row = db.get(MatchEvidence, evidence_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Evidência não encontrada")
    path = pathlib.Path(row.stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo da evidência não encontrado")
    return FileResponse(
        path,
        media_type=row.content_type or "application/octet-stream",
        filename=row.original_name or path.name,
    )


@router.get("/revisao", response_class=HTMLResponse)
def review_page(
    request: Request,
    msg: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    matches = review_queue(db)
    names = _club_name_map(
        db,
        {m.club_a_id for m in matches} | {m.club_b_id for m in matches},
    )
    classifications = classification_map(db, [m.match_id for m in matches])
    rows = []
    for match in matches:
        club_match = db.get(
            ClubMatch, {"club_id": settings.CLUB_ID, "match_id": match.match_id}
        )
        rows.append(
            match_row(
                club_match,
                match,
                names,
                classifications.get(match.match_id),
            )
        )
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "matches": rows,
            "club_name": settings.CLUB_NAME,
            "club_id": settings.CLUB_ID,
            "review_status_labels": STATUS_LABELS,
            "msg": msg,
            "pending": sum(
                1 for m in matches if m.review_status == "needs_review"
            ),
            "ignored": sum(1 for m in matches if m.review_status == "ignored"),
        },
    )


@router.post("/revisao/reanalisar")
def review_rescan(db: Session = Depends(get_db)) -> RedirectResponse:
    summary = scan_matches(db)
    db.commit()
    message = (
        f"Reanálise concluída: {summary['scanned']} partida(s), "
        f"{summary['changed']} alterada(s), {summary['ignored']} ignorada(s), "
        f"{summary['needs_review']} para revisar."
    )
    return RedirectResponse(url=f"/revisao?msg={quote(message)}", status_code=303)


@router.get("/campeonatos", response_class=HTMLResponse)
def championships_list(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    rows = db.query(Championship).order_by(Championship.created_at.desc()).all()
    items = []
    for championship in rows:
        item = _championship_view(db, championship)
        item["campaign"] = build_campaign(
            championship_rows(db, settings.CLUB_ID, championship.id)
        )
        items.append(item)

    return templates.TemplateResponse(
        request,
        "championships.html",
        {
            "championships": items,
            "club_name": settings.CLUB_NAME,
            "format_labels": FORMAT_LABELS,
            "status_labels": STATUS_LABELS,
        },
    )


@router.post("/campeonatos")
def championship_create(
    name: str = Form(...),
    championship_format: str = Form("league", alias="format"),
    status: str = Form("active"),
    start_date: str = Form(""),
    end_date: str = Form(""),
    color: str = Form("#3b82f6"),
    description: str = Form(""),
    external_url: str = Form(""),
    extra_json: str = Form("", alias="extra"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    clean_name = name.strip()
    if not clean_name:
        return RedirectResponse(url="/campeonatos?erro=Nome+obrigat%C3%B3rio", status_code=303)

    extra_value = None
    if extra_json.strip():
        try:
            parsed = json.loads(extra_json)
        except ValueError:
            return RedirectResponse(url="/campeonatos?erro=JSON+inv%C3%A1lido", status_code=303)
        if not isinstance(parsed, dict):
            return RedirectResponse(url="/campeonatos?erro=JSON+inv%C3%A1lido", status_code=303)
        extra_value = serialize_extra(parsed)

    now = now_iso()
    championship = Championship(
        name=clean_name,
        format=championship_format if championship_format in FORMAT_LABELS else "other",
        status=status if status in STATUS_LABELS else "active",
        start_date=_clean(start_date),
        end_date=_clean(end_date),
        color=color or "#3b82f6",
        description=_clean(description),
        external_url=_clean(external_url),
        extra=extra_value,
        created_at=now,
        updated_at=now,
    )
    db.add(championship)
    db.commit()
    db.refresh(championship)
    return RedirectResponse(url=f"/campeonatos/{championship.id}", status_code=303)


@router.get("/campeonatos/{championship_id}", response_class=HTMLResponse)
def championship_detail(
    championship_id: int,
    request: Request,
    erro: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    championship = db.get(Championship, championship_id)
    if championship is None:
        raise HTTPException(status_code=404, detail=f"Campeonato {championship_id} não encontrado")

    rows = championship_rows(db, settings.CLUB_ID, championship.id)
    campaign = build_campaign(rows)
    names = _club_name_map(
        db,
        {m.club_a_id for _, m in rows} | {m.club_b_id for _, m in rows},
    )
    classifications = classification_map(db, [m.match_id for _, m in rows])
    matches_out = [
        match_row(cm, m, names, classifications.get(m.match_id)) for cm, m in rows
    ]

    return templates.TemplateResponse(
        request,
        "championship_detail.html",
        {
            "championship": _championship_view(db, championship),
            "campaign": campaign,
            "matches": matches_out,
            "top_scorers": top_scorers(db, settings.CLUB_ID, [m.match_id for _, m in rows]),
            "opponents": opponent_summary(db, settings.CLUB_ID, rows),
            "stages": stage_breakdown(stage_rows(db, settings.CLUB_ID, championship.id)),
            "club_name": settings.CLUB_NAME,
            "format_labels": FORMAT_LABELS,
            "status_labels": STATUS_LABELS,
            "leg_labels": LEG_LABELS,
            "erro": erro,
        },
    )


@router.post("/campeonatos/{championship_id}/editar")
def championship_update(
    championship_id: int,
    name: str = Form(...),
    championship_format: str = Form("league", alias="format"),
    status: str = Form("active"),
    start_date: str = Form(""),
    end_date: str = Form(""),
    color: str = Form("#3b82f6"),
    description: str = Form(""),
    external_url: str = Form(""),
    extra_json: str = Form("", alias="extra"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    championship = db.get(Championship, championship_id)
    if championship is None:
        raise HTTPException(status_code=404, detail=f"Campeonato {championship_id} não encontrado")

    clean_name = name.strip()
    if not clean_name:
        return RedirectResponse(
            url=f"/campeonatos/{championship_id}?erro=Nome+obrigat%C3%B3rio",
            status_code=303,
        )

    if extra_json.strip():
        try:
            parsed = json.loads(extra_json)
        except ValueError:
            return RedirectResponse(
                url=f"/campeonatos/{championship_id}?erro=JSON+inv%C3%A1lido",
                status_code=303,
            )
        if not isinstance(parsed, dict):
            return RedirectResponse(
                url=f"/campeonatos/{championship_id}?erro=JSON+inv%C3%A1lido",
                status_code=303,
            )
        championship.extra = serialize_extra(parsed)
    else:
        championship.extra = None

    championship.name = clean_name
    championship.format = (
        championship_format if championship_format in FORMAT_LABELS else "other"
    )
    championship.status = status if status in STATUS_LABELS else "active"
    championship.start_date = _clean(start_date)
    championship.end_date = _clean(end_date)
    championship.color = color or "#3b82f6"
    championship.description = _clean(description)
    championship.external_url = _clean(external_url)
    championship.updated_at = now_iso()
    db.commit()
    return RedirectResponse(url=f"/campeonatos/{championship_id}", status_code=303)


@router.post("/campeonatos/{championship_id}/arquivar")
def championship_archive(
    championship_id: int,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    championship = db.get(Championship, championship_id)
    if championship is None:
        raise HTTPException(status_code=404, detail=f"Campeonato {championship_id} não encontrado")
    championship.status = "archived"
    championship.updated_at = now_iso()
    db.commit()
    return RedirectResponse(url=f"/campeonatos/{championship_id}", status_code=303)


@router.post("/campeonatos/{championship_id}/excluir")
def championship_delete(
    championship_id: int,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    championship = db.get(Championship, championship_id)
    if championship is None:
        raise HTTPException(status_code=404, detail=f"Campeonato {championship_id} não encontrado")

    classified = (
        db.query(MatchClassification)
        .filter(MatchClassification.championship_id == championship_id)
        .count()
    )
    if classified:
        message = (
            f"Este campeonato tem {classified} partida(s) classificada(s). "
            "Desclassifique ou arquive antes de excluir."
        )
        return RedirectResponse(
            url=f"/campeonatos/{championship_id}?erro={quote(message)}",
            status_code=303,
        )

    db.delete(championship)
    db.commit()
    return RedirectResponse(url="/campeonatos", status_code=303)


@router.post("/partidas/{match_id}/classificar")
def classify_match_form(
    match_id: str,
    championship_id: int = Form(...),
    stage: str = Form(""),
    round_name: str = Form("", alias="round"),
    leg: str = Form(""),
    group_name: str = Form(""),
    tags: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")
    if db.get(Championship, championship_id) is None:
        raise HTTPException(status_code=404, detail=f"Campeonato {championship_id} não encontrado")

    _apply_form_classification(
        db,
        match_id,
        championship_id,
        stage,
        round_name,
        leg,
        group_name,
        tags,
        notes,
    )
    db.commit()
    return RedirectResponse(url=f"/partidas/{match_id}", status_code=303)


@router.post("/partidas/{match_id}/desclassificar")
def unclassify_match_form(
    match_id: str,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    classification = db.get(MatchClassification, match_id)
    if classification is not None:
        db.delete(classification)
        db.flush()
        auto_review_match(db, match_id)
        db.commit()
    return RedirectResponse(url=f"/partidas/{match_id}", status_code=303)


@router.post("/partidas/classificar-lote")
def bulk_classify_form(
    championship_id: int = Form(...),
    match_ids: list[str] = Form(default=[]),
    stage: str = Form(""),
    round_name: str = Form("", alias="round"),
    leg: str = Form(""),
    group_name: str = Form(""),
    tags: str = Form(""),
    notes: str = Form(""),
    next_url: str = Form("/partidas"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    target = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/partidas"
    if db.get(Championship, championship_id) is None:
        raise HTTPException(status_code=404, detail=f"Campeonato {championship_id} não encontrado")

    for match_id in match_ids:
        if db.get(Match, match_id) is None:
            continue
        _apply_form_classification(
            db,
            match_id,
            championship_id,
            stage,
            round_name,
            leg,
            group_name,
            tags,
            notes,
        )
    db.commit()
    return RedirectResponse(url=target, status_code=303)


@router.get("/jogadores", response_class=HTMLResponse)
def players_list(
    request: Request,
    posicao: str | None = None,
    min_jogos: int = 0,
    ordem: str = "power",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    club_id = settings.CLUB_ID
    filters = MatchFilters.from_query(request.query_params)
    rows = filtered_matches(db, club_id, filters)
    players = player_aggregates(db, club_id, rows)
    squad = squad_view(db, club_id, players, platform=settings.PLATFORM)

    cards = list(squad["cards"])
    if posicao in POSITION_LABELS:
        cards = [card for card in cards if card["position_group"] == posicao]
    if min_jogos > 0:
        cards = [
            card
            for card in cards
            if (card["local"]["matches"] if card["local"] else 0) >= min_jogos
        ]

    def _card_key(card: dict[str, object]) -> tuple:
        local = card.get("local") or {}
        member = card.get("member")
        if ordem == "overall":
            return (-int(getattr(member, "overall", 0) or 0), str(card.get("member") and member.name or ""))
        if ordem == "name":
            name = getattr(member, "name", None) or local.get("playername") or ""
            return (str(name).casefold(),)
        if ordem == "goals":
            return (-int(local.get("goals", 0) or 0), -int(local.get("minutes", 0) or 0))
        if ordem == "rating":
            return (-float(local.get("avg_rating") or 0), -int(local.get("minutes", 0) or 0))
        if ordem == "ga90":
            return (-float(local.get("involvement_per90") or 0), -int(local.get("minutes", 0) or 0))
        return (-float(local.get("power_index") or 0), -int(local.get("minutes", 0) or 0))

    cards.sort(key=_card_key)

    scatter = [
        {
            "player_key": player["player_key"],
            "name": player["playername"],
            "x": player["goals_per90"],
            "y": player["assists_per90"],
            "minutes": player["minutes"],
            "group": player["position_group"],
            "rating": player["avg_rating"],
        }
        for player in players
        if player["minutes"] >= 30
    ]
    player_options = [
        {"player_key": player["player_key"], "playername": player["playername"]}
        for player in sorted(players, key=lambda item: item["playername"].casefold())
    ]

    return templates.TemplateResponse(
        request,
        "players.html",
        {
            "club_name": settings.CLUB_NAME,
            "club_id": club_id,
            "cards": cards,
            "squad": squad,
            "counts": squad["counts"],
            "position_counts": squad["position_counts"],
            "scatter": scatter,
            "players": players,
            "player_options": player_options,
            "posicao": posicao or "",
            "min_jogos": min_jogos,
            "ordem": ordem,
            "opponents": _opponent_options(db, club_id),
            **_filters_context(filters),
        },
    )


@router.get("/analise", response_class=HTMLResponse)
def analysis_page(
    request: Request,
    ordem: str = "power",
    min_min: int = 0,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    club_id = settings.CLUB_ID
    filters = MatchFilters.from_query(request.query_params)
    rows = filtered_matches(db, club_id, filters)
    names = _names_for_rows(db, rows)
    players = player_aggregates(db, club_id, rows)
    profile = team_profile(db, club_id, rows, players)
    visible = [player for player in players if player["minutes"] >= max(min_min, 0)]
    visible = sort_players(visible, ordem)
    directions = pass_direction_players(players)
    sessions = session_reports(db, club_id, rows)

    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "club_name": settings.CLUB_NAME,
            "club_id": club_id,
            "summary": analytics.local_summary(rows),
            "official": official_summary(
                _overall_for(db, club_id, settings.PLATFORM), names
            ),
            "chart": analytics.goals_chart(rows, club_id, names),
            "profile": profile,
            "directions": directions,
            "sessions": sessions,
            "players": visible,
            "all_players": players,
            "min_min": min_min,
            "ordem": ordem,
            "opponents": _opponent_options(db, club_id),
            "total_matches": len(rows),
            **_filters_context(filters),
        },
    )


@router.get("/h2h", response_class=HTMLResponse)
def h2h_page(
    request: Request,
    adversario: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    club_id = settings.CLUB_ID
    filters = MatchFilters.from_query(request.query_params)
    rows = filtered_matches(db, club_id, filters)
    opponents = _opponent_options(db, club_id)
    selected = adversario if adversario else None
    if selected and not any(item["club_id"] == selected for item in opponents):
        selected = None

    context: dict[str, object] = {
        "club_name": settings.CLUB_NAME,
        "club_id": club_id,
        "opponents": opponents,
        "selected": selected,
        "official": official_summary(
            _overall_for(db, club_id, settings.PLATFORM), _club_name_map(db, {club_id})
        ),
        "me": _club_name_map(db, {club_id}).get(club_id, settings.CLUB_NAME),
        **_filters_context(filters),
    }
    if selected:
        summary = h2h_summary(rows, club_id, selected)
        opponent_overall = _overall_for(db, selected, settings.PLATFORM)
        names = _club_name_map(db, {club_id, selected})
        context.update(
            {
                "h2h": summary,
                "opponent_name": names.get(selected, selected),
                "opponent_official": official_summary(
                    opponent_overall, _club_name_map(db, {selected})
                ),
                "opponent_refreshed": opponent_overall is not None,
                "names": names,
            }
        )
    return templates.TemplateResponse(request, "h2h.html", context)


@router.post("/h2h/{opponent_id}/atualizar")
def h2h_refresh(
    opponent_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Fetch and store the opponent's EA season snapshot on demand."""
    from app.worker.ingestor import sync_club_profile
    from app.worker.scheduler import _make_ea_client

    try:
        sync_club_profile(
            opponent_id,
            db,
            _make_ea_client(),
            platform=settings.PLATFORM,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - network/manual action
        db.rollback()
        return RedirectResponse(
            url=f"/h2h?adversario={quote(opponent_id)}&erro={quote(str(exc))}",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/h2h?adversario={quote(opponent_id)}&msg=Dados+do+advers%C3%A1rio+atualizados",
        status_code=303,
    )


@router.post("/vincular-membro")
def link_member_form(
    membro: str = Form(...),
    player_key: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if db.get(Player, player_key) is None:
        return RedirectResponse(
            url="/jogadores?erro=Jogador+inv%C3%A1lido", status_code=303
        )
    try:
        link_member(
            db,
            settings.CLUB_ID,
            settings.PLATFORM,
            membro,
            player_key,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        return RedirectResponse(
            url=f"/jogadores?erro={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(
        url="/jogadores?msg=V%C3%ADnculo+salvo", status_code=303
    )


@router.get("/jogadores/{player_key}", response_class=HTMLResponse)
def player_detail(
    player_key: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    club_id = settings.CLUB_ID
    filters = MatchFilters.from_query(request.query_params)
    rows = filtered_matches(db, club_id, filters)
    names = _names_for_rows(db, rows)
    rows_by_id = {match.match_id: (club_match, match) for club_match, match in rows}

    player = db.get(Player, player_key)
    aggregates = player_aggregates(db, club_id, rows)
    summary = next(
        (item for item in aggregates if item["player_key"] == player_key), None
    )

    metrics = _metrics_by_match(db, list(rows_by_id), player_key)
    stat_rows = (
        db.query(PlayerMatchStats)
        .filter(
            PlayerMatchStats.player_key == player_key,
            PlayerMatchStats.match_id.in_(list(rows_by_id)),
        )
        .all()
    )
    history: list[dict[str, object]] = []
    for stat in stat_rows:
        club_match, match = rows_by_id[stat.match_id]
        opponent = analytics.opponent_id(match, club_id)
        metric = metrics.get(stat.match_id)
        history.append(
            {
                "match_id": stat.match_id,
                "played_at": match.played_at,
                "opponent_id": opponent,
                "opponent_name": names.get(opponent, opponent),
                "goals_for": club_match.goals_for,
                "goals_against": club_match.goals_against,
                "letter": analytics.result_letter(club_match),
                "type_label": match_type_label(match.match_type_code),
                "position": stat.position,
                "minutes": int((stat.seconds_played or 0) // 60),
                "rating": stat.rating,
                "goals": stat.goals or 0,
                "assists": stat.assists or 0,
                "shots": stat.shots or 0,
                "passes_made": stat.passes_made or 0,
                "pass_attempts": stat.pass_attempts or 0,
                "tackles_made": stat.tackles_made or 0,
                "saves": stat.saves or 0,
                "is_mom": bool(stat.is_mom),
                "has_metrics": metric is not None,
                "events": {
                    "passes": getattr(metric, "passes_completed", None),
                    "dribbles": getattr(metric, "dribbles_completed", None),
                    "interceptions": getattr(metric, "interceptions", None),
                    "press_wins": getattr(metric, "possession_won_attacking_third", None),
                }
                if metric is not None
                else {},
            }
        )
    history.sort(key=lambda item: str(item["played_at"]), reverse=True)
    chart_history = list(reversed(history))
    rating_values = [item["rating"] for item in chart_history]

    member = (
        db.query(ClubMember)
        .filter(
            ClubMember.club_id == club_id,
            ClubMember.platform == settings.PLATFORM,
            ClubMember.player_key == player_key,
        )
        .first()
    )
    if member is None and summary is not None:
        target = normalize_name(str(summary["playername"]))
        member = next(
            (
                candidate
                for candidate in db.query(ClubMember).filter(
                    ClubMember.club_id == club_id,
                    ClubMember.platform == settings.PLATFORM,
                )
                if normalize_name(candidate.name) == target
            ),
            None,
        )
    career = (
        db.query(ClubMemberCareer)
        .filter(
            ClubMemberCareer.club_id == club_id,
            ClubMemberCareer.platform == settings.PLATFORM,
            ClubMemberCareer.player_key == player_key,
        )
        .first()
    )
    if career is None and member is not None:
        career = db.get(
            ClubMemberCareer,
            {
                "club_id": club_id,
                "platform": settings.PLATFORM,
                "name": member.name,
            },
        )

    return templates.TemplateResponse(
        request,
        "player_detail.html",
        {
            "player": player,
            "player_key": player_key,
            "summary": summary,
            "history": history,
            "chart_labels": [item["played_at"][:10] if item["played_at"] else "" for item in chart_history],
            "chart_values": rating_values,
            "member": member,
            "career": career,
            "member_name": member.name if member is not None else None,
            "display_name": (
                summary["playername"]
                if summary is not None
                else member.name
                if member is not None
                else player_key
            ),
            "club_name": settings.CLUB_NAME,
            "club_id": club_id,
            **_filters_context(filters),
        },
    )


@router.get("/telemetria", response_class=HTMLResponse)
def telemetry(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    club_id = settings.CLUB_ID
    filters = MatchFilters.from_query(request.query_params)
    rows = filtered_matches(db, club_id, filters)
    players = player_aggregates(db, club_id, rows)
    profile = team_profile(db, club_id, rows, players)
    directions = pass_direction_players(players)
    coverage = analytics.event_coverage(
        db, [match.match_id for _, match in rows]
    )

    event_players = [player for player in players if player["has_events"]]
    event_players.sort(
        key=lambda player: (
            -int(player["passes_completed_events"] or 0),
            -int(player["minutes"] or 0),
        )
    )
    team_totals = [
        {
            "label": analytics.METRIC_LABELS.get(field, field),
            "field": field,
            "value": value,
        }
        for field, value in sorted(
            (profile.get("totals") or {}).items(),
            key=lambda item: -int(item[1]),
        )
        if value
    ][:16]

    return templates.TemplateResponse(
        request,
        "telemetry.html",
        {
            "club_name": settings.CLUB_NAME,
            "club_id": club_id,
            "players": event_players,
            "all_players": players,
            "profile": profile,
            "directions": directions,
            "coverage": coverage,
            "team_totals": team_totals,
            "match_count": len(rows),
            "opponents": _opponent_options(db, club_id),
            **_filters_context(filters),
        },
    )


@router.get("/status", response_class=HTMLResponse)
def status(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    runs = db.query(IngestRun).order_by(IngestRun.started_at.desc()).limit(20).all()
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "runs": runs,
            "club_name": settings.CLUB_NAME,
            "current_user": user,
            "is_pro": bool(user and user.plan_tier == "pro"),
            "user_plan": user.plan_tier if user else "free",
        },
    )


# ---------------------------------------------------------------------------
# Páginas de Autenticação, Planos e Gestão de Clubes
# ---------------------------------------------------------------------------


@router.get("/cadastro", response_class=HTMLResponse)
def register_page(
    request: Request,
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "register.html",
        {"club_name": settings.CLUB_NAME, "current_user": None, "user_plan": "anonymous"},
    )


@router.post("/cadastro", response_class=HTMLResponse)
def register_form(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    club_id: str | None = Form(None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    email_clean = email.strip().lower()
    existing = db.query(User).filter(User.email == email_clean).first()
    if existing:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "club_name": settings.CLUB_NAME,
                "current_user": None,
                "user_plan": "anonymous",
                "email": email,
                "erro": "Este e-mail já está cadastrado. Faça login na sua conta.",
            },
            status_code=400,
        )

    if len(password) < 6:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "club_name": settings.CLUB_NAME,
                "current_user": None,
                "user_plan": "anonymous",
                "email": email,
                "erro": "A senha deve conter no mínimo 6 caracteres.",
            },
            status_code=400,
        )

    salt, pw_hash = hash_password(password)
    now_str = now_iso()
    new_user = User(
        email=email_clean,
        password_hash=pw_hash,
        salt=salt,
        plan_tier="free",
        created_at=now_str,
        updated_at=now_str,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    tracked_cid = None
    tracked_cname = None
    if club_id and club_id.strip():
        cid_or_name = club_id.strip()
        if cid_or_name.isdigit():
            tracked_cid = cid_or_name
            tracked_cname = f"Clube {tracked_cid}"
        else:
            # Busca o ID na EA pelo nome digitado
            search_matches = search_ea_clubs_by_name(cid_or_name)
            if search_matches:
                tracked_cid = search_matches[0]["club_id"]
                tracked_cname = search_matches[0]["name"]

        if tracked_cid:
            tc = TrackedClub(
                user_id=new_user.id,
                club_id=tracked_cid,
                platform="common-gen5",
                club_name=tracked_cname,
                is_active=1,
                created_at=now_str,
            )
            db.add(tc)
            db.commit()

    token = create_user_session(db, new_user.id)
    if tracked_cid:
        redirect_url = f"/dashboard?club_id={tracked_cid}&msg=Conta+criada+com+sucesso!+Clube+{quote(tracked_cname or tracked_cid)}+conectado."
    else:
        redirect_url = "/meus-clubes?onboarding=1&msg=Conta+criada+com+sucesso!+Encontre+seu+time+pelo+nome+abaixo."

    response = RedirectResponse(url=redirect_url, status_code=303)
    response.set_cookie(key="fc27_session", value=token, httponly=True, max_age=30 * 86400, samesite="lax")
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"club_name": settings.CLUB_NAME, "current_user": None, "user_plan": "anonymous"},
    )


@router.post("/login", response_class=HTMLResponse)
def login_form(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    email_clean = email.strip().lower()
    user = db.query(User).filter(User.email == email_clean).first()
    if not user or not verify_password(password, user.salt, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "club_name": settings.CLUB_NAME,
                "current_user": None,
                "user_plan": "anonymous",
                "erro": "E-mail ou senha inválidos.",
            },
            status_code=401,
        )

    token = create_user_session(db, user.id)
    response = RedirectResponse(url="/?msg=Bem-vindo+de+volta!", status_code=303)
    response.set_cookie(key="fc27_session", value=token, httponly=True, max_age=30 * 86400, samesite="lax")
    return response


@router.get("/logout")
def logout_action(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> RedirectResponse:
    token = request.cookies.get("fc27_session")
    if token:
        delete_session(db, token)
    response = RedirectResponse(url="/login?msg=Você+saiu+da+sua+conta.", status_code=303)
    response.delete_cookie(key="fc27_session")
    return response


@router.get("/planos", response_class=HTMLResponse)
def plans_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    is_pro = bool(user and user.plan_tier == "pro")
    active_order = None
    if user:
        active_order = (
            db.query(PaymentOrder)
            .filter(PaymentOrder.user_id == user.id, PaymentOrder.status == "pending")
            .order_by(PaymentOrder.created_at.desc())
            .first()
        )
    return templates.TemplateResponse(
        request,
        "plans.html",
        {
            "club_name": settings.CLUB_NAME,
            "current_user": user,
            "is_pro": is_pro,
            "user_plan": user.plan_tier if user else "anonymous",
            "pro_price": "39,90",
            "active_order": active_order,
        },
    )


@router.get("/meus-clubes", response_class=HTMLResponse)
def tracked_clubs_page(
    request: Request,
    q: str | None = None,
    platform: str = "common-gen5",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    if not user:
        return RedirectResponse(url="/login?erro=Faça+login+para+gerenciar+seus+clubes.", status_code=303)

    tracked = db.query(TrackedClub).filter(TrackedClub.user_id == user.id, TrackedClub.is_active == 1).all()
    search_results = []
    clean_q = (q or "").strip()
    if clean_q:
        search_results = search_ea_clubs_by_name(clean_q, platform=platform)

    return templates.TemplateResponse(
        request,
        "tracked_clubs.html",
        {
            "club_name": settings.CLUB_NAME,
            "current_user": user,
            "is_pro": user.plan_tier == "pro",
            "user_plan": user.plan_tier,
            "tracked": tracked,
            "search_query": clean_q,
            "search_results": search_results,
            "onboarding": request.query_params.get("onboarding") == "1",
        },
    )


@router.post("/meus-clubes/adicionar")
def add_tracked_club(
    request: Request,
    club_id: str = Form(...),
    club_name: str | None = Form(None),
    platform: str = Form("common-gen5"),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> RedirectResponse:
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    cid = club_id.strip()
    cname = club_name.strip() if club_name else None
    if cid:
        if not cname:
            ea_data = fetch_ea_public_club(cid, platform)
            cname = ea_data.get("name") or f"Clube {cid}"

        existing = db.query(TrackedClub).filter(TrackedClub.user_id == user.id, TrackedClub.club_id == cid).first()
        if existing:
            existing.is_active = 1
            if cname:
                existing.club_name = cname
        else:
            tc = TrackedClub(
                user_id=user.id,
                club_id=cid,
                club_name=cname,
                platform=platform,
                is_active=1,
                created_at=now_iso(),
            )
            db.add(tc)
        db.commit()

    dest_name = quote(cname or cid)
    return RedirectResponse(
        url=f"/dashboard?club_id={cid}&msg=Clube+{dest_name}+adicionado+com+sucesso!+Iniciando+monitoramento.",
        status_code=303,
    )


@router.post("/meus-clubes/remover/{club_id}")
def remove_tracked_club(
    club_id: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
) -> RedirectResponse:
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    tc = db.query(TrackedClub).filter(TrackedClub.user_id == user.id, TrackedClub.club_id == club_id.strip()).first()
    if tc:
        db.delete(tc)
        db.commit()

    return RedirectResponse(url="/meus-clubes?msg=Clube+removido+do+monitoramento.", status_code=303)


@router.get("/explorar", response_class=HTMLResponse)
def explore_public_page(
    request: Request,
    club_id: str | None = None,
    platform: str = "common-gen5",
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> HTMLResponse:
    snapshot = None
    if club_id and club_id.strip():
        snapshot = fetch_ea_public_club(club_id.strip(), platform)

    return templates.TemplateResponse(
        request,
        "explore.html",
        {
            "club_name": settings.CLUB_NAME,
            "current_user": user,
            "is_pro": bool(user and user.plan_tier == "pro"),
            "user_plan": user.plan_tier if user else "anonymous",
            "searched_id": club_id or "",
            "snapshot": snapshot,
        },
    )

