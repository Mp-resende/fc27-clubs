"""Result review API: detection, ignore/confirm/correct and print evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.config import Settings
from app.core.evidence import (
    EvidenceError,
    extract_summary,
    save_image,
)
from app.core.review import (
    STATUS_IGNORED,
    STATUS_NEEDS_REVIEW,
    SOURCE_MANUAL,
    SOURCE_SCREENSHOT,
    apply_manual_result,
    auto_review_match,
    confirm_match,
    ignore_match,
    now_iso,
    review_queue,
    review_view,
    scan_matches,
)
from app.db.models import Club, Match, MatchEvidence

router = APIRouter(prefix="/api/v1", tags=["review"])


class ReviewNote(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class ManualResultIn(BaseModel):
    club_a_goals: int = Field(ge=0, le=99)
    club_b_goals: int = Field(ge=0, le=99)
    note: str | None = Field(default=None, max_length=2000)
    source: Literal["manual", "screenshot"] = "manual"


def _get_match(db: Session, match_id: str) -> Match:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Partida {match_id!r} não encontrada")
    return match


def _match_view(db: Session, match: Match) -> dict[str, object]:
    names = dict(
        db.query(Club.club_id, Club.name)
        .filter(Club.club_id.in_([match.club_a_id, match.club_b_id]))
        .all()
    )
    return {
        "match_id": match.match_id,
        "played_at": match.played_at,
        "match_type_code": match.match_type_code,
        "club_a_id": match.club_a_id,
        "club_a_name": names.get(match.club_a_id, match.club_a_id),
        "club_b_id": match.club_b_id,
        "club_b_name": names.get(match.club_b_id, match.club_b_id),
        "club_a_goals": match.club_a_goals,
        "club_b_goals": match.club_b_goals,
        "dnf": match.dnf,
        "review": review_view(match),
    }


@router.get("/review/queue")
def get_review_queue(db: Session = Depends(get_db)) -> dict[str, object]:
    matches = review_queue(
        db, statuses=(STATUS_NEEDS_REVIEW, STATUS_IGNORED)
    )
    return {"items": [_match_view(db, match) for match in matches]}


@router.post("/review/scan")
def rescan_matches(db: Session = Depends(get_db)) -> dict[str, object]:
    summary = scan_matches(db)
    db.commit()
    return summary


@router.get("/matches/{match_id}/review")
def get_match_review(match_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    match = _get_match(db, match_id)
    return _match_view(db, match)


@router.post("/matches/{match_id}/review/confirm")
def confirm_match_result(
    match_id: str,
    payload: ReviewNote | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    match = _get_match(db, match_id)
    confirm_match(db, match, payload.note if payload else None)
    db.commit()
    return _match_view(db, match)


@router.post("/matches/{match_id}/review/ignore")
def ignore_match_result(
    match_id: str,
    payload: ReviewNote | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    match = _get_match(db, match_id)
    ignore_match(db, match, payload.note if payload else None)
    db.commit()
    return _match_view(db, match)


@router.post("/matches/{match_id}/review/restore")
def restore_match_result(
    match_id: str,
    payload: ReviewNote | None = None,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    match = _get_match(db, match_id)
    confirm_match(db, match, payload.note if payload else None)
    auto_review_match(db, match_id)
    db.commit()
    return _match_view(db, match)


@router.post("/matches/{match_id}/review/correct")
def correct_match_result(
    match_id: str,
    payload: ManualResultIn,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    match = _get_match(db, match_id)
    apply_manual_result(
        db,
        match,
        payload.club_a_goals,
        payload.club_b_goals,
        source=payload.source or SOURCE_MANUAL,
        note=payload.note,
    )
    db.commit()
    return _match_view(db, match)


@router.post("/matches/{match_id}/review/evidence")
async def upload_match_evidence(
    match_id: str,
    file: UploadFile = File(...),
    apply: bool = Form(False),
    note: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    match = _get_match(db, match_id)
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    names = dict(
        db.query(Club.club_id, Club.name)
        .filter(Club.club_id.in_([match.club_a_id, match.club_b_id]))
        .all()
    )
    extraction = extract_summary(
        Path(stored.stored_path),
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
            source=SOURCE_SCREENSHOT,
            note=note or "Resultado extraído do print da partida.",
        )
        applied = True

    db.commit()
    return {
        "match_id": match_id,
        "evidence_id": evidence.id,
        "extractor": extraction.extractor,
        "applied": applied,
        "data": extraction.data,
        "warnings": extraction.warnings,
        "review": review_view(match),
    }


@router.get("/matches/{match_id}/review/evidence")
def list_match_evidence(
    match_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _get_match(db, match_id)
    rows = (
        db.query(MatchEvidence)
        .filter(MatchEvidence.match_id == match_id)
        .order_by(MatchEvidence.created_at.desc())
        .all()
    )
    items = []
    for row in rows:
        extracted: object | None = None
        if row.extracted_json:
            try:
                extracted = json.loads(row.extracted_json)
            except ValueError:
                extracted = None
        items.append(
            {
                "id": row.id,
                "original_name": row.original_name,
                "content_type": row.content_type,
                "size_bytes": row.size_bytes,
                "sha256": row.sha256,
                "extractor": row.extractor,
                "created_at": row.created_at,
                "extracted": extracted,
            }
        )
    return {"match_id": match_id, "items": items}
