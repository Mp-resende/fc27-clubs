"""Private championships: CRUD plus per-match and bulk classification."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.api.schemas.championship import (
    BulkClassificationCreate,
    BulkClassificationResult,
    ChampionshipCreate,
    ChampionshipOut,
    ChampionshipUpdate,
    ClassificationCreate,
    ClassificationOut,
)
from app.config import Settings
from app.core.championships import (
    build_campaign,
    championship_rows,
    classification_map,
    match_row,
    now_iso,
    opponent_summary,
    parse_extra,
    serialize_extra,
    serialize_tags,
    stage_breakdown,
    stage_rows,
    top_scorers,
)
from app.db.models import Championship, Club, Match, MatchClassification
from app.core.review import auto_review_match

router = APIRouter(prefix="/api/v1", tags=["championships"])


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _get_championship(db: Session, championship_id: int) -> Championship:
    championship = db.get(Championship, championship_id)
    if championship is None:
        raise HTTPException(status_code=404, detail=f"Campeonato {championship_id} não encontrado")
    return championship


def _championship_out(
    db: Session,
    championship: Championship,
    club_id: str,
) -> dict[str, object]:
    rows = championship_rows(db, club_id, championship.id)
    campaign = build_campaign(rows)
    return {
        "id": championship.id,
        "name": championship.name,
        "format": championship.format,
        "status": championship.status,
        "start_date": championship.start_date,
        "end_date": championship.end_date,
        "color": championship.color,
        "description": championship.description,
        "external_url": championship.external_url,
        "extra": parse_extra(championship.extra),
        "created_at": championship.created_at,
        "updated_at": championship.updated_at,
        "matches": campaign["played"],
        "campaign": campaign,
    }


def _classification_out(db: Session, match_id: str) -> ClassificationOut | None:
    view = classification_map(db, [match_id]).get(match_id)
    if view is None:
        return None
    return ClassificationOut(**view)


def _apply_classification(
    db: Session,
    match_id: str,
    payload: ClassificationCreate,
) -> MatchClassification:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")
    _get_championship(db, payload.championship_id)

    now = now_iso()
    classification = db.get(MatchClassification, match_id)
    if classification is None:
        classification = MatchClassification(match_id=match_id, updated_at=now)
        db.add(classification)

    classification.championship_id = payload.championship_id
    classification.stage = _clean(payload.stage)
    classification.round = _clean(payload.round)
    classification.leg = _clean(payload.leg)
    classification.group_name = _clean(payload.group_name)
    classification.notes = _clean(payload.notes)
    classification.tags = serialize_tags(payload.tags)
    classification.updated_at = now
    # Group-stage context changes how a 3x0 scoreline is interpreted.
    db.flush()
    auto_review_match(db, match_id)
    return classification


# ── Championship CRUD ────────────────────────────────────────────────────


@router.get("/championships")
def list_championships(
    status_filter: str | None = Query(default=None, alias="status"),
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    q = db.query(Championship)
    if status_filter:
        q = q.filter(Championship.status == status_filter)
    elif not include_archived:
        q = q.filter(Championship.status != "archived")

    championships = q.order_by(Championship.created_at.desc()).all()
    items = [_championship_out(db, item, settings.CLUB_ID) for item in championships]
    return {"items": items}


@router.post("/championships", status_code=201, response_model=ChampionshipOut)
def create_championship(
    payload: ChampionshipCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    now = now_iso()
    championship = Championship(
        name=payload.name.strip(),
        format=payload.format,
        status=payload.status,
        start_date=_clean(payload.start_date),
        end_date=_clean(payload.end_date),
        color=payload.color or "#3b82f6",
        description=_clean(payload.description),
        external_url=_clean(payload.external_url),
        extra=serialize_extra(payload.extra),
        created_at=now,
        updated_at=now,
    )
    db.add(championship)
    db.commit()
    db.refresh(championship)
    return _championship_out(db, championship, settings.CLUB_ID)


@router.get("/championships/{championship_id}", response_model=ChampionshipOut)
def get_championship(
    championship_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    championship = _get_championship(db, championship_id)
    return _championship_out(db, championship, settings.CLUB_ID)


@router.patch("/championships/{championship_id}", response_model=ChampionshipOut)
def update_championship(
    championship_id: int,
    payload: ChampionshipUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    championship = _get_championship(db, championship_id)
    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"] is not None:
        championship.name = str(data["name"]).strip()
    for field in ("format", "status"):
        if field in data and data[field] is not None:
            setattr(championship, field, data[field])
    for field in ("start_date", "description", "external_url", "color"):
        if field in data:
            setattr(championship, field, _clean(data[field]))
    if "end_date" in data:
        championship.end_date = _clean(data["end_date"])
    if "extra" in data:
        championship.extra = serialize_extra(data["extra"])

    championship.updated_at = now_iso()
    db.commit()
    db.refresh(championship)
    return _championship_out(db, championship, settings.CLUB_ID)


@router.delete("/championships/{championship_id}", status_code=204)
def delete_championship(
    championship_id: int,
    db: Session = Depends(get_db),
) -> Response:
    championship = _get_championship(db, championship_id)
    classified = (
        db.query(MatchClassification)
        .filter(MatchClassification.championship_id == championship_id)
        .count()
    )
    if classified:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Campeonato possui {classified} partida(s) classificada(s). "
                "Desclassifique-as ou arquive o campeonato."
            ),
        )
    db.delete(championship)
    db.commit()
    return Response(status_code=204)


# ── Championship detail data ─────────────────────────────────────────────


@router.get("/championships/{championship_id}/matches")
def list_championship_matches(
    championship_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _get_championship(db, championship_id)
    rows = championship_rows(db, settings.CLUB_ID, championship_id)
    names = dict(db.query(Club.club_id, Club.name).all())
    classifications = classification_map(db, [m.match_id for _, m in rows])
    items = [
        match_row(cm, match, names, classifications.get(match.match_id))
        for cm, match in rows
    ]
    return {
        "items": items,
        "campaign": build_campaign(rows),
        "top_scorers": top_scorers(
            db, settings.CLUB_ID, [m.match_id for _, m in rows]
        ),
        "opponents": opponent_summary(db, settings.CLUB_ID, rows),
        "stages": stage_breakdown(stage_rows(db, settings.CLUB_ID, championship_id)),
    }


# ── Classification ───────────────────────────────────────────────────────


@router.get("/matches/{match_id}/classification", response_model=ClassificationOut | None)
def get_match_classification(
    match_id: str,
    db: Session = Depends(get_db),
) -> ClassificationOut | None:
    if db.get(Match, match_id) is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")
    return _classification_out(db, match_id)


@router.post("/matches/{match_id}/classification", response_model=ClassificationOut)
def classify_match(
    match_id: str,
    payload: ClassificationCreate,
    db: Session = Depends(get_db),
) -> ClassificationOut:
    _apply_classification(db, match_id, payload)
    db.commit()
    result = _classification_out(db, match_id)
    assert result is not None
    return result


@router.delete("/matches/{match_id}/classification", status_code=204)
def unclassify_match(
    match_id: str,
    db: Session = Depends(get_db),
) -> Response:
    classification = db.get(MatchClassification, match_id)
    if classification is not None:
        db.delete(classification)
        db.flush()
        auto_review_match(db, match_id)
        db.commit()
    return Response(status_code=204)


@router.post("/matches/bulk-classify", response_model=BulkClassificationResult)
def bulk_classify(
    payload: BulkClassificationCreate,
    db: Session = Depends(get_db),
) -> BulkClassificationResult:
    _get_championship(db, payload.championship_id)

    classified = 0
    skipped: list[str] = []
    for match_id in payload.match_ids:
        if db.get(Match, match_id) is None:
            skipped.append(match_id)
            continue
        _apply_classification(db, match_id, payload)
        classified += 1

    db.commit()
    return BulkClassificationResult(
        championship_id=payload.championship_id,
        classified=classified,
        skipped=skipped,
    )
